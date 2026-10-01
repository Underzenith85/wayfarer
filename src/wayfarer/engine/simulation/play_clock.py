"""Explicit full-state clock dependency for engine transitions.

A resource-only caller keeps the historical path. Persisted hosts inject their
chronological checkpoint function so body and actor changes survive advancement.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.resources import Advance

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine

PlayClock = Callable[[PlayState, Advance, RandomSource], PlayState]


def advance_play(
    engine: ResourceEngine,
    state: PlayState,
    command: Advance,
    rng: RandomSource,
    clock: PlayClock | None = None,
) -> PlayState:
    if clock is not None:
        return clock(state, command, rng)
    resources = engine.apply(state.resources, command, system=True, rng=rng)
    return state.model_copy(update={"resources": resources})
