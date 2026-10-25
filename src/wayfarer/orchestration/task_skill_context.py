"""Captured prerequisite facts for ordinary and long-task preparation."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from wayfarer.orchestration.noncombat_generations import skill_prerequisite_context

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService

KEY = "skill_prerequisite_context_generation"
KINDS = frozenset(
    {
        "begin-check",
        "prepare-secret-check",
        "choose-secret-check",
        "begin-work",
        "accept-check",
        "use-luck",
    }
)
_current: ContextVar[bool] = ContextVar(KEY, default=False)


def enabled() -> bool:
    return _current.get()


@contextmanager
def generation(correct: bool) -> Iterator[None]:
    token = _current.set(correct)
    try:
        yield
    finally:
        _current.reset(token)


async def capture(play: PlayService, cid: str, command_id: str, kind: str) -> bool:
    return kind in KINDS and await skill_prerequisite_context(play, cid, command_id)
