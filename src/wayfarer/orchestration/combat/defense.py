"""Committing the defender's choice."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TypedCombatCommand
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.lite_resolution import resolve_injury
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.combat.handlers import _unarmed


def _defend(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    assert isinstance(command, ChooseDefense)
    if encounter.pending_unarmed is not None:
        return _unarmed(state, command, encounter, context)
    if command.catch_thrown:
        from wayfarer.engine.simulation.combat.thrown.items import validate_catch

        validate_catch(play.rules_context, state, encounter, command)
    from wayfarer.engine.simulation.abilities import interrupt_concentration
    from wayfarer.engine.simulation.magic.effects import require_not_dazed

    if command.defense != "none":
        require_not_dazed(resources, command.actor_id)
        resources = interrupt_concentration(
            resources, command.actor_id, command.id, distraction=True
        )
        state = state.model_copy(update={"resources": resources})
    previous = encounter
    selected_defense = command.defense
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.melee.resolution import resolve_melee

        pending = encounter.pending_defense
        if (
            pending is None
            or pending.defender_id != command.actor_id
            or command.defense not in pending.allowed
        ):
            raise ValidationError("Defense is not available to this actor")
        from wayfarer.engine.simulation.combat.melee.defense import (
            exert_defense,
            validate_defense_choices,
        )

        validate_defense_choices(
            play.rules_context,
            state,
            encounter,
            selected_defense,
            command.item_id,
            command.second_defense,
            command.second_item_id,
            parry_mode_id=command.parry_mode_id,
            second_parry_mode_id=command.second_parry_mode_id,
        )
        state, encounter, selected_defense = exert_defense(
            play.rules_context,
            state,
            encounter,
            command.actor_id,
            command.id,
            selected_defense,
            command.item_id,
            parry_mode_id=command.parry_mode_id,
        )
        state, encounter, injury = resolve_melee(
            play.rules_context,
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
        )
        from wayfarer.engine.simulation.actors import injury_turn

        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        if (
            not attacker.maneuver_state.attacks_remaining
            and (encounter.wait_interrupt is None or not encounter.wait_interrupt.reacting)
            and not (encounter.pending_defense and encounter.pending_defense.spray_targets)
            and pending.suppression_zone_id is None
        ):
            state = injury_turn(
                play.rules_context,
                state,
                pending.attacker_id,
                pending.id,
                start=False,
                do_nothing=False,
            )
        if pending.suppression_zone_id is not None and not any(
            any(
                zone.id == queued.zone_id and zone.remaining_hits > 0
                for zone in encounter.suppression_zones
            )
            for queued in pending.suppression_attacks
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
    elif (
        command.item_id is not None
        or command.second_defense is not None
        or command.second_item_id is not None
    ):
        raise ValidationError("Defense equipment selection requires GURPS dispatch")
    encounter, result = engine.choose_defense(
        encounter, actor_id=command.actor_id, selected=selected_defense
    )
    if encounter.pending_defense is not None and engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.melee.attack import prepare_attack

        queued = encounter.pending_defense
        encounter = prepare_attack(
            play.rules_context,
            state,
            encounter,
            queued.mode_id,
            hit_location=queued.hit_location,
            target_item_id=queued.target_item_id,
            shots=queued.shots,
        )
    if engine.rules.attacks or engine.rules.gurps_equipment is not None:
        if engine.rules.gurps_equipment is None:
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
