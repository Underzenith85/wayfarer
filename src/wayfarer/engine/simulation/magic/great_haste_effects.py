"""B251 extra maneuver projection and one forced end-of-spell fatigue consequence."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.magic.great_haste_state import (
    ACTIVATION,
    ENDED,
    GreatHasteActivation,
    GreatHasteEnd,
    save,
)
from wayfarer.engine.simulation.magic.spell_state import active_spells, latest
from wayfarer.engine.simulation.resources import ResourceState

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.rules_context import RulesContext


def bonus(resources: ResourceState, actor_id: str) -> int:
    return int(
        any(
            e.spell_id == "great-haste"
            and e.target_id == actor_id
            and e.execute_effects
            and not e.reversed
            for e in active_spells(resources)
        )
    )


def checkpoint(runtime: RulesContext, state: PlayState) -> PlayState:
    resources = state.resources
    effects = latest(resources)
    ended = {
        GreatHasteEnd.model_validate_json(e.kind).cast_id
        for e in resources.events
        if e.id.startswith(ENDED)
    }
    for event in state.resources.events:
        if not event.id.startswith(ACTIVATION):
            continue
        activation = GreatHasteActivation.model_validate_json(event.kind)
        effect = effects.get(activation.cast_id)
        if (
            activation.cast_id in ended
            or effect is None
            or (effect.phase == "active" and resources.game_time < activation.expires_at)
        ):
            continue
        fp_lost = hp_lost = 0
        if activation.actor_id != activation.target_id:
            resources, result = apply_fatigue(
                resources,
                FatigueCost(
                    id="great-haste-end:" + activation.cast_id,
                    actor_id=activation.target_id,
                    expected_revision=resources.revision,
                    amount=5,
                    power=True,
                ),
                ht=activation.ht,
                will=activation.will,
                rng=runtime.rng,
                system=True,
            )
            fp_lost, hp_lost = result.fp_lost, result.hp_lost
        resources = save(
            resources,
            ENDED,
            activation.cast_id,
            activation.target_id,
            GreatHasteEnd(
                cast_id=activation.cast_id,
                fp_lost=fp_lost,
                hp_lost=hp_lost,
            ),
        )
        ended.add(activation.cast_id)
    return state.model_copy(
        update={"resources": resources.model_copy(update={"revision": state.resources.revision})}
    )


def deadlines(resources: ResourceState) -> tuple[int, ...]:
    """Force full-state clock checkpoints at each enrolled spell's end."""
    ended = {
        GreatHasteEnd.model_validate_json(e.kind).cast_id
        for e in resources.events
        if e.id.startswith(ENDED)
    }
    return tuple(
        GreatHasteActivation.model_validate_json(e.kind).expires_at
        for e in resources.events
        if e.id.startswith(ACTIVATION)
        and GreatHasteActivation.model_validate_json(e.kind).cast_id not in ended
    )
