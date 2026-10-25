"""Canonical no-dice attack specifications, shared with actual resolution."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.attack_policy import policy, uses_basic_set_attacks
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee
from wayfarer.engine.simulation.combat.ranged.resolution import resolve as resolve_ranged
from wayfarer.engine.simulation.combat.ranged.spray_sequence import following as following_spray
from wayfarer.engine.simulation.combat.ranged_target_policy import (
    bind_pending as bind_ranged_pending,
)
from wayfarer.engine.simulation.combat.ranged_target_policy import (
    enabled as ranged_postures_enabled,
)
from wayfarer.engine.simulation.combat.sensory_combat import refresh_armed_senses
from wayfarer.engine.simulation.combat.unarmed.resolution import defend as unarmed_spec
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.magic.missiles import resolve as resolve_missile
from wayfarer.engine.simulation.rules_context import RulesContext


def inventory_spec(runtime: RulesContext, state: PlayState, encounter: Encounter) -> AttackRollSpec:
    pending = encounter.pending_defense
    assert pending is not None
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode):
        return resolve_ranged(
            runtime,
            state,
            encounter,
            weapon,
            "none",
            None,
            second_defense=None,
            second_item_id=None,
            prepare_only=True,
        )
    return resolve_melee(
        runtime, state, encounter, "none", None, prepare_only=True, admission_only=True
    )


def require_pending_minimum(
    runtime: RulesContext, state: PlayState, encounter: Encounter, *, captured: bool = False
) -> tuple[str, AttackRollSpec] | None:
    """Only an unrolled voluntary producer opts in; fragments and strays do not."""
    if captured:
        return None
    posture_spec = _ranged_posture_spec(runtime, state, encounter)
    _remaining_spray_specs(runtime, state, encounter)
    if not uses_basic_set_attacks(runtime):
        return posture_spec
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    if unarmed is not None:
        if not policy(runtime, state, encounter, unarmed.actor_id, pending_id=unarmed.id).minimum:
            return None
        spec = unarmed_spec(
            runtime,
            state,
            encounter,
            ChooseDefense(
                id=unarmed.id,
                actor_id=unarmed.target_id,
                expected_revision=state.revision,
                encounter_id=encounter.id,
                defense="none",
            ),
            prepare_only=True,
            admission_only=True,
        )
        spec.require_voluntary()
        return unarmed.id, spec
    if pending is None or (
        pending.attack_roll is not None
        or pending.composed_attack_id is not None
        or pending.spell_cast_id is not None
        or pending.suppression_zone_id is not None
        or pending.shield_rush
    ):
        return posture_spec
    if policy(runtime, state, encounter, pending.attacker_id, pending_id=pending.id).minimum:
        spec = (
            posture_spec[1]
            if posture_spec is not None
            else inventory_spec(runtime, state, encounter)
        )
        spec.require_voluntary()
        return pending.id, spec
    return posture_spec


def _ranged_posture_spec(
    runtime: RulesContext, state: PlayState, encounter: Encounter
) -> tuple[str, AttackRollSpec] | None:
    pending = encounter.pending_defense
    if pending is None or pending.attack_roll is not None or pending.composed_attack_id is not None:
        return None
    if not ranged_postures_enabled(
        runtime, state, encounter, pending.attacker_id, pending_id=pending.id
    ):
        return None
    spec = (
        resolve_missile(runtime, state, encounter, "none", None, None, None, prepare_only=True)
        if pending.spell_cast_id is not None
        else inventory_spec(runtime, state, encounter)
    )
    return pending.id, spec


def _remaining_spray_specs(runtime: RulesContext, state: PlayState, encounter: Encounter) -> None:
    pending = encounter.pending_defense
    if pending is None or not ranged_postures_enabled(
        runtime, state, encounter, pending.attacker_id, pending_id=pending.id
    ):
        return
    minimum = policy(runtime, state, encounter, pending.attacker_id, pending_id=pending.id).minimum
    future = following_spray(pending)
    while future is not None:
        projected = encounter.model_copy(update={"pending_defense": future})
        projected = refresh_armed_senses(
            runtime,
            state,
            projected,
            ChooseDefense(
                id=future.id,
                actor_id=future.defender_id,
                expected_revision=state.revision,
                encounter_id=encounter.id,
                defense="none",
            ),
            attack_captured=False,
        )
        candidate = bind_ranged_pending(runtime, state, projected)
        spec = inventory_spec(runtime, candidate, projected)
        if minimum:
            spec.require_voluntary()
        future = following_spray(future)
