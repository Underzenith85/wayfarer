"""Committing the defender's choice."""

from __future__ import annotations

from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import injury_turn
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TypedCombatCommand
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter, PendingDefense
from wayfarer.engine.simulation.combat.lite_resolution import resolve_injury
from wayfarer.engine.simulation.combat.maneuver_budget import last_opportunity
from wayfarer.engine.simulation.combat.melee.attack import prepare_attack
from wayfarer.engine.simulation.combat.melee.defense import exert_defense, validate_defense_choices
from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.shield_rush import resolve as resolve_shield_rush
from wayfarer.engine.simulation.combat.thrown.items import validate_catch
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.traits.composed_phases import ComposedAttackChoice
from wayfarer.engine.simulation.traits.composed_resolution import resolve
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.handlers import _unarmed
from wayfarer.orchestration.melee_spell_contacts import capture_pending_contact


def _failed_interposition(
    state: PlayState,
    encounter: Encounter,
    pending: PendingDefense,
    previous: Encounter,
    context: CombatContext,
    injury: InjuryTrace | None = None,
) -> CombatStep:
    restored = pending.model_copy(
        update={
            "defender_id": pending.protected_defender_id,
            "protected_defender_id": None,
            "attack_roll": injury.attack if injury else pending.attack_roll,
        }
    )
    encounter = encounter.model_copy(update={"pending_defense": restored})
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.sacrificial_failed",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
        available=context.engine.available(encounter, encounter.current_actor_id),
        injury=injury,
    )
    return CombatStep(state, encounter, state.resources, result, defense_before=previous)


def _settled_choice(
    command: ChooseDefense,
    encounter: Encounter,
    selected_defense: Literal["dodge", "parry", "block", "none"],
) -> tuple[str, Literal["dodge", "parry", "block", "none"]]:
    selected_actor = command.actor_id
    if (
        command.sacrificial_for
        and encounter.pending_defense
        and encounter.pending_defense.defender_id != command.actor_id
    ):
        selected_actor = encounter.pending_defense.defender_id
        selected_defense = "none"
    return selected_actor, selected_defense


def _drop_friend(encounter: Encounter, pending: PendingDefense, injury: InjuryTrace) -> Encounter:
    if (
        pending.sacrificial_drop
        and pending.protected_defender_id
        and injury.defense
        and injury.defense.outcome.succeeded
    ):
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(update={"posture": "prone"})
                    if p.actor_id == pending.protected_defender_id
                    else p
                    for p in encounter.participants
                )
            }
        )
    return encounter


def _non_inventory_defense(
    state: PlayState, command: ChooseDefense, encounter: Encounter, context: CombatContext
) -> CombatStep | None:
    if (
        encounter.pending_defense is not None
        and encounter.pending_defense.composed_attack_id is not None
    ):
        if context.selected_attack is not None and not isinstance(
            context.selected_attack, ComposedAttackChoice
        ):
            raise ValidationError("Captured attack source does not match this composed response")
        resolved = resolve(
            context.play.rules_context,
            state,
            encounter,
            command,
            selected_attack=context.selected_attack,
        )
        return CombatStep(
            resolved.state,
            resolved.encounter,
            resolved.state.resources,
            resolved.result,
            defense_before=encounter,
        )
    if encounter.pending_unarmed is not None:
        return _unarmed(state, command, encounter, context)
    return None


def _defend(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    assert isinstance(command, ChooseDefense)
    non_inventory = _non_inventory_defense(state, command, encounter, context)
    if non_inventory is not None:
        return non_inventory
    if command.catch_thrown:
        validate_catch(context.attack_runtime, state, encounter, command)

    if command.defense != "none":
        require_not_dazed(resources, command.actor_id)
        resources = interrupt_concentration(
            resources, command.actor_id, command.id, distraction=True
        )
        state = state.model_copy(update={"resources": resources})
    previous = encounter
    selected_defense = command.defense
    if engine.rules.gurps_equipment is not None:
        pending = encounter.pending_defense
        if (
            pending is None
            or pending.defender_id != command.actor_id
            or command.defense not in pending.allowed
        ):
            raise ValidationError("Defense is not available to this actor")

        validate_defense_choices(
            context.attack_runtime,
            state,
            encounter,
            selected_defense,
            command.item_id,
            command.second_defense,
            command.second_item_id,
            parry_mode_id=command.parry_mode_id,
            second_parry_mode_id=command.second_parry_mode_id,
            incoming_item_id=pending.weapon_id,
            incoming_mode_id=pending.mode_id,
        )
        state, encounter, selected_defense = exert_defense(
            context.attack_runtime,
            state,
            encounter,
            command.actor_id,
            command.id,
            selected_defense,
            command.item_id,
            parry_mode_id=command.parry_mode_id,
            incoming_item_id=pending.weapon_id,
            incoming_mode_id=pending.mode_id,
        )
        if pending.protected_defender_id and selected_defense == "none":
            return _failed_interposition(state, encounter, pending, previous, context)
        if pending.shield_rush:
            if command.second_defense is not None or command.catch_thrown:
                raise ValidationError("Shield rush accepts one ordinary active defense")
            state, encounter, injury = resolve_shield_rush(
                context.attack_runtime,
                state,
                encounter,
                selected_defense,
                command.item_id,
                selected_attack=context.selected_attack.selected
                if context.selected_attack
                else None,
            )
        else:
            state, encounter, injury = resolve_melee(
                context.attack_runtime,
                state,
                encounter,
                selected_defense,
                command.item_id,
                second_defense=command.second_defense if selected_defense != "none" else None,
                second_item_id=command.second_item_id if selected_defense != "none" else None,
                parry_mode_id=command.parry_mode_id if selected_defense != "none" else None,
                second_parry_mode_id=command.second_parry_mode_id
                if selected_defense != "none"
                else None,
                catch_thrown=command.catch_thrown,
                selected_attack=context.selected_attack.selected
                if context.selected_attack
                else None,
            )

        return finish_inventory_defense(
            state,
            command,
            encounter,
            context,
            selected_defense=selected_defense,
            pending=pending,
            previous=previous,
            injury=injury,
            captured_end_build=(
                context.selected_attack.captured_attacker if context.selected_attack else None
            ),
        )
    elif (
        command.item_id is not None
        or command.second_defense is not None
        or command.second_item_id is not None
    ):
        raise ValidationError("Defense equipment selection requires GURPS dispatch")
    selected_actor, selected_defense = _settled_choice(command, encounter, selected_defense)
    encounter, result = engine.choose_defense(
        encounter, actor_id=selected_actor, selected=selected_defense
    )
    if engine.rules.attacks:
        state, injury = resolve_injury(play.rules_context, state, previous, command.defense)
        resources = state.resources
        encounter = encounter.model_copy(update={"wounds": encounter.wounds + (injury,)})
        result = result.model_copy(
            update={
                "code": "combat.resolved",
                "injury": injury,
                "round": encounter.round,
                "current_actor_id": encounter.current_actor_id,
                "available": engine.available(encounter, encounter.current_actor_id),
            }
        )
    return CombatStep(state, encounter, resources, result, defense_before=previous)


def finish_inventory_defense(
    state: PlayState,
    command: ChooseDefense,
    encounter: Encounter,
    context: CombatContext,
    *,
    selected_defense: Literal["dodge", "parry", "block", "none"],
    pending: PendingDefense,
    previous: Encounter,
    injury: InjuryTrace,
    captured_end_build: ValidatedBuild | None = None,
) -> CombatStep:
    """Finish a weapon response after its one canonical injury resolution."""
    play, engine = context.play, context.engine
    current_attacker = next(a for a in state.actors if a.actor_id == pending.attacker_id)
    if current_attacker.approval is not None:
        captured_end_build = None
    encounter = _drop_friend(encounter, pending, injury)

    if (
        pending.protected_defender_id
        and injury.attack.outcome.succeeded
        and injury.defense is not None
        and not injury.defense.outcome.succeeded
    ):
        return _failed_interposition(state, encounter, pending, previous, context, injury)

    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    if (
        not attacker.maneuver_state.attacks_remaining
        and (encounter.wait_interrupt is None or not encounter.wait_interrupt.reacting)
        and not (encounter.pending_defense and encounter.pending_defense.spray_targets)
        and pending.suppression_zone_id is None
        and last_opportunity(encounter)
    ):
        state = injury_turn(
            play.rules_context,
            state,
            pending.attacker_id,
            pending.id,
            start=False,
            do_nothing=False,
            captured_end_build=captured_end_build,
        )
    if (
        last_opportunity(encounter)
        and pending.suppression_zone_id is not None
        and not any(
            any(
                zone.id == queued.zone_id and zone.remaining_hits > 0
                for zone in encounter.suppression_zones
            )
            for queued in pending.suppression_attacks
        )
    ):
        assert pending.interrupted_actor_id is not None
        state = injury_turn(
            play.rules_context,
            state,
            pending.interrupted_actor_id,
            pending.id,
            start=False,
            do_nothing=False,
        )
    resources = state.resources
    selected_actor, selected_defense = _settled_choice(command, encounter, selected_defense)
    encounter, result = engine.choose_defense(
        encounter, actor_id=selected_actor, selected=selected_defense
    )
    if encounter.pending_defense is not None:
        queued = encounter.pending_defense
        encounter = prepare_attack(
            play.rules_context,
            state,
            encounter,
            queued.mode_id,
            hit_location=queued.hit_location,
            armor_chink=queued.armor_chink,
            strike_strength=queued.strike_strength,
            subdual_mode=queued.subdual_mode,
            target_item_id=queued.target_item_id,
            shots=queued.shots,
        )
        state = capture_pending_contact(play.rules_context, state, encounter, command.id)
        resources = state.resources
    encounter = encounter.model_copy(update={"wounds": encounter.wounds + (injury,)})
    result = result.model_copy(
        update={
            "code": "combat.resolved",
            "injury": injury,
            "round": encounter.round,
            "current_actor_id": encounter.current_actor_id,
            "available": engine.available(encounter, encounter.current_actor_id),
        }
    )
    return CombatStep(state, encounter, resources, result, defense_before=previous)
