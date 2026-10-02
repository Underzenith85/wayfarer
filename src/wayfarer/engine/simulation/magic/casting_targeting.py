"""B239 roll-time targeting while preserving the accepted casting commitment."""

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.magic.lock_effects import finalize_lock_skill
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEffect
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.magic.spells import SpellContext

PREFIX = "casting-targeting:"


class CastingTargeting(Record):
    cast_id: str
    distance: int
    unseen: bool


def remember_targeting(
    state: ResourceState,
    effect: RuntimeSpellEffect,
    context: SpellContext,
    *,
    kind: str,
    enabled: bool = True,
    item_sight: bool = True,
) -> ResourceState:
    if (
        not enabled
        or context.execution_version != 2
        and not (context.item_cast and item_sight)
        or kind not in ("regular", "resisted")
        or effect.spell_id in ("lockmaster", "magelock")
    ):
        return state
    value = CastingTargeting(
        cast_id=effect.cast_id, distance=context.distance, unseen=context.unseen
    )
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(effect.cast_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id=effect.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def completion_targeting(
    state: ResourceState, effect: RuntimeSpellEffect, context: SpellContext
) -> RuntimeSpellEffect:
    if effect.spell_id in ("lockmaster", "magelock"):
        return finalize_lock_skill(state, effect, context)
    original = next(
        (
            CastingTargeting.model_validate_json(e.kind)
            for e in state.events
            if e.id == PREFIX + hashlib.sha256(effect.cast_id.encode()).hexdigest()
        ),
        None,
    )
    if original is None:
        return effect  # Historical checkpoints preserve their original execution.
    return effect.model_copy(
        update={
            "skill": effect.skill
            + original.distance
            - context.distance
            + 5 * (int(original.unseen) - int(context.unseen)),
            "position": context.position,
        }
    )
