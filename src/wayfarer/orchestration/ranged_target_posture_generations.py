"""Capture B551 ranged target posture policy independently for launched attacks."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import (
    ranged_target_posture_generation,
    stamp_ranged_target_postures,
)
from wayfarer.persistence.events import CommandInput

ACTIVE = True

_current: ContextVar[bool | None] = ContextVar("ranged_target_posture_generation", default=None)


def correct_ranged_target_postures() -> bool:
    current = _current.get()
    if current is not None:
        return current
    prior = recorded_command.get()
    return (
        ACTIVE
        if prior is None
        else ranged_target_posture_generation(CommandInput(prior.payload_hash, prior.command_input))
    )


@contextmanager
def ranged_target_posture_scope(correct: bool) -> Iterator[None]:
    token = _current.set(correct)
    try:
        yield
    finally:
        _current.reset(token)


async def capture(store: Store, cid: str, command_id: str, text: str) -> tuple[str, bool]:
    replay = recorded_command.get()
    prior = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await store.command_input(cid, command_id)
    )
    correct = ACTIVE if prior is None else ranged_target_posture_generation(prior)
    return (stamp_ranged_target_postures(text) if correct else text), correct
