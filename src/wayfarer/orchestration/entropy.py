"""Command-scoped entropy at the transaction boundary, never on shared engines."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from contextvars import ContextVar
from copy import deepcopy
from typing import Protocol

from wayfarer.contracts import Campaign, CommandReceipt, TurnResult
from wayfarer.engine.rules.checks import RandomSource, draw_index
from wayfarer.engine.rules.randomness import RNG_ALGORITHM, SeededRandom
from wayfarer.engine.simulation.events import command_events
from wayfarer.errors import ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.origins import current_origin
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import SessionRegistry, Store
from wayfarer.persistence.events import (
    CommandEntropy,
    CommandOrigin,
    CommandResolution,
    payload_digest,
)

SeedSource = Callable[[], str]


class CommandBoundary(Protocol):
    """What a command needs from the service that owns it, and nothing more.

    ``PlayService`` is the boundary in production. Declaring it structurally keeps
    the entropy module below the services and lets the narrower resource service
    commit through the same lock, clock and seed source.
    """

    store: Store
    sessions: SessionRegistry
    instants: Callable[[], CommandInstant]
    seeds: SeedSource


_active: ContextVar[RandomSource | None] = ContextVar("command_random", default=None)


def token_seed() -> str:
    """The production seed source; a runtime injects a scripted one in tests."""
    return secrets.token_hex(32)


class CommandRandom:
    """Passable RNG handle; a synchronous resolver's scope wins over fixture injection.

    Context is owned by orchestration. Domain code only sees the RandomSource
    protocol. Tokens are reset even on failure; concurrent tasks never share RNGs.
    """

    def __init__(self, injected: RandomSource | None = None) -> None:
        self.injected: RandomSource | None
        self.injected = injected.injected if isinstance(injected, CommandRandom) else injected
        if self.injected is secrets:
            self.injected = None

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        source = _active.get() or self.injected
        if source is None:
            raise ValidationError("Random resolution requires a command scope")
        return draw_index(source, exclusive_upper_bound)


async def commit_command(
    play: CommandBoundary,
    cid: str,
    request_id: str,
    revision: int,
    text: str,
    resolve: Callable[[Campaign], CommandReceipt],
    *,
    actor_id: str = "system",
    rng: RandomSource | None = None,
    instant: CommandInstant | None = None,
    origin: CommandOrigin | None = None,
) -> TurnResult:

    async with play.sessions.serialized(cid):
        return await _commit_serialized(
            play,
            cid,
            request_id,
            revision,
            text,
            resolve,
            actor_id=actor_id,
            rng=rng,
            instant=instant,
            origin=origin,
        )


async def _commit_serialized(
    play: CommandBoundary,
    cid: str,
    request_id: str,
    revision: int,
    text: str,
    resolve: Callable[[Campaign], CommandReceipt],
    *,
    actor_id: str = "system",
    rng: RandomSource | None = None,
    instant: CommandInstant | None = None,
    origin: CommandOrigin | None = None,
) -> TurnResult:
    """Capture entropy and time before storage; retries return the winning receipt.

    The instant and seed sources belong to the runtime that built this service, so a
    test scripts them by construction. Explicit scripted RNG sources remain useful
    for rule fixtures. Such records carry an injected algorithm marker and never
    claim seed-only re-executability.
    """
    if not actor_id:
        raise ValidationError("Command requires a principal")
    replay = recorded_command.get()
    handle = CommandRandom(rng)
    if replay is not None:
        if not replay.reexecutable or replay.recorded_at_us is None or handle.injected is not None:
            raise ValidationError("Command lacks compatible replay inputs")
        if (cid, request_id, revision, actor_id, payload_digest({"input": text})) != (
            replay.campaign_id,
            replay.command_id,
            replay.expected_revision,
            replay.actor_id,
            replay.payload_hash,
        ):
            raise ValidationError("Replay command identity or payload mismatch")
        assert replay.entropy_seed is not None and replay.rng_algorithm is not None
        instant = CommandInstant(replay.recorded_at_us)
        entropy = CommandEntropy(replay.entropy_seed, replay.rng_algorithm)
        origin = replay.origin
    else:
        instant = instant if instant is not None else play.instants()
        entropy = CommandEntropy(
            seed=play.seeds(),
            rng_algorithm="injected" if handle.injected is not None else RNG_ALGORITHM,
        )
    source = handle.injected or SeededRandom(entropy.seed)

    def run(state: Campaign) -> CommandResolution:
        token = _active.set(source)
        try:
            before = deepcopy(state)
            event = resolve(state)
            family = event["action"]
            return CommandResolution(event, command_events(before, state, family, actor_id))
        finally:
            _active.reset(token)

    return await play.store.commit_turn(
        cid,
        request_id,
        revision,
        text,
        run,
        actor_id=actor_id,
        entropy=entropy,
        recorded_at_us=instant.unix_microseconds,
        origin=origin if origin is not None else current_origin.get(),
    )
