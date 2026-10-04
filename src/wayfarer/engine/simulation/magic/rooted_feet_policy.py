"""Captured private Rooted Feet admission policy; absence preserves generation one."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.errors import ValidationError

RootedGeneration = Literal[1, 2]
_GENERATION: ContextVar[RootedGeneration] = ContextVar("rooted_feet_generation", default=1)


@contextmanager
def rooted_generation(generation: int) -> Iterator[None]:
    """Only the authenticated service/replay envelope selects this policy."""
    if type(generation) is not int or generation not in (1, 2):
        raise ValidationError("Unsupported Rooted Feet generation")
    selected: RootedGeneration = 1 if generation == 1 else 2
    token = _GENERATION.set(selected)
    try:
        yield
    finally:
        _GENERATION.reset(token)


def allows_haste() -> bool:
    return _GENERATION.get() == 2


def qualified_haste_bonus(state: PlayState, actor_id: str) -> int | None:
    """Strongest supported live cross-actor personal Haste, or unsupported.

    Canonical Haste item channels require self-targeting. Cross-actor effects
    therefore prove the personal carrier without inferring a current channel.
    Subject-maintained spells and all other spell shapes remain unsupported.
    """
    effects = tuple(
        e
        for e in active_spells(state.resources)
        if e.actor_id == actor_id or e.target_id == actor_id
    )
    if any(
        e.spell_id != "haste"
        or e.actor_id == actor_id
        or e.target_id != actor_id
        or not e.execute_effects
        or e.reversed
        or e.energy not in (1, 2, 3)
        or e.expires_at is None
        or e.expires_at <= state.resources.game_time
        for e in effects
    ):
        return None
    return max((e.energy for e in effects), default=0)
