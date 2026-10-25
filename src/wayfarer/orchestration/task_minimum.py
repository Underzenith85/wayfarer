"""Independent B345 generation for fresh staged check and work producers."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from wayfarer.orchestration.noncombat_generations import minimum_effective_skill

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService

KEY = "minimum_effective_skill_generation"
KINDS = frozenset({"begin-check", "prepare-secret-check", "begin-work"})
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
    return kind in KINDS and await minimum_effective_skill(play, cid, command_id)
