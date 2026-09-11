"""Command-scoped entropy at the transaction boundary, never on shared engines."""

import secrets
from collections.abc import Callable
from contextvars import ContextVar

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event, TurnResult
from wayfarer.orchestration.clock import CommandInstant, capture_instant
from wayfarer.orchestration.origins import current_origin
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandEntropy, CommandOrigin
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.checks import RandomSource, draw_index
from wayfarer.rules.randomness import RNG_ALGORITHM, SeededRandom

_active: ContextVar[RandomSource | None] = ContextVar("command_random", default=None)


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
    store: AsyncSQLiteStore | AsyncPostgresStore,
    cid: str,
    request_id: str,
    revision: int,
    text: str,
    resolve: Callable[[Campaign], Event],
    *,
    actor_id: str = "system",
    rng: RandomSource | None = None,
    instant: CommandInstant | None = None,
    origin: CommandOrigin | None = None,
) -> TurnResult:
    """Capture entropy and time before storage; retries return the winning receipt.

    Explicit scripted sources remain useful for rule fixtures. Such records carry
    an injected algorithm marker and never claim seed-only re-executability.
    """
    if not actor_id:
        raise ValidationError("Command requires a principal")
    instant = instant if instant is not None else capture_instant()
    handle = CommandRandom(rng)
    entropy = CommandEntropy(
        seed=secrets.token_hex(32),
        rng_algorithm="injected" if handle.injected is not None else RNG_ALGORITHM,
    )
    source = handle.injected or SeededRandom(entropy.seed)

    def run(state: Campaign) -> Event:
        token = _active.set(source)
        try:
            return resolve(state)
        finally:
            _active.reset(token)

    return await store.commit_turn(
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
