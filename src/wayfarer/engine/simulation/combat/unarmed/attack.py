"""Executing a declared unarmed attack."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog, exertion, injury_turn
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter, move_basic
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.unarmed.control import control
from wayfarer.engine.simulation.combat.unarmed.declaration import interrupt_wait, validate_action
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, settle_control
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed, require_basic
from wayfarer.engine.simulation.combat.unarmed.resolution import defend
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeUnarmedTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def execute_unarmed(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeUnarmedTurn | ChooseDefense,
) -> tuple[PlayState, Encounter, CombatResult]:
    from wayfarer.engine.simulation.combat.commands import ChooseDefense

    require_basic(catalog(runtime).profile_id)
    # A declared Wait reaction borrows the interrupted turn; it is not a second turn.
    reacting = encounter.wait_interrupt is not None
    if isinstance(command, ChooseDefense):
        state, encounter, trace = defend(runtime, state, encounter, command)
    else:
        if reacting:
            assert encounter.wait_interrupt is not None
            encounter = encounter.model_copy(
                update={
                    "turn_index": encounter.turn_order.index(command.actor_id),
                    "wait_interrupt": encounter.wait_interrupt.model_copy(
                        update={"reacting": True}
                    ),
                }
            )
        validate_action(runtime, state, encounter, command)
        if not reacting:
            fired = interrupt_wait(runtime, state, encounter, command)
            if fired is not None:
                return state, fired[0], fired[1]
        if command.action in ("release", "lock_damage"):
            state, encounter, trace = control(runtime, state, encounter, command)
            encounter = settle_control(state, encounter)
            encounter = encounter.model_copy(
                update={"unarmed_history": encounter.unarmed_history + (trace,)}
            )
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.grip_released",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    unarmed=trace,
                ),
            )
        actor = fighter(encounter, command.actor_id)
        if not reacting:
            state = injury_turn(
                runtime,
                state,
                actor.actor_id,
                command.id,
                start=True,
                do_nothing=actor.forced_do_nothing,
            )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
        assert hp.injury is not None
        allowed = not (hp.injury.incapacitated or hp.injury.stunned or actor.forced_do_nothing)
        if allowed:
            state, allowed = exertion(runtime, state, actor.actor_id, command.id)
        if not allowed:
            if not reacting:
                state = injury_turn(
                    runtime, state, actor.actor_id, command.id, start=False, do_nothing=True
                )
            encounter = CombatEngine._replace(
                encounter,
                actor.model_copy(
                    update={
                        "forced_do_nothing": False,
                        "last_maneuver": "do_nothing",
                        "maneuver_state": ManeuverState(),
                    }
                ),
            )
            encounter = CombatEngine._advance(encounter)
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.unarmed_unavailable",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                ),
            )
        previous = actor.maneuver_state
        actor = actor.model_copy(
            update={
                "last_maneuver": command.maneuver,
                "maneuver_state": ManeuverState(
                    evaluate_target_id=previous.evaluate_target_id
                    if actor.last_maneuver == "evaluate"
                    else None,
                    evaluate_bonus=previous.evaluate_bonus
                    if actor.last_maneuver == "evaluate"
                    else 0,
                    feint_target_id=previous.feint_target_id
                    if actor.last_maneuver == "feint"
                    else None,
                    feint_penalty=previous.feint_penalty if actor.last_maneuver == "feint" else 0,
                    defense_forbidden=command.maneuver == "all_out_attack",
                    parry_forbidden=command.maneuver == "move_and_attack",
                    attack_bonus=4
                    if command.attack_option == "determined"
                    else -4
                    if command.maneuver == "move_and_attack"
                    else 0,
                    attack_cap=9 if command.maneuver == "move_and_attack" else None,
                    strong=command.attack_option == "strong",
                ),
            }
        )
        encounter = CombatEngine._replace(encounter, actor)
        if command.action in ("punch", "kick", "grapple", "arm_lock"):
            if command.enter_close_combat:
                target = fighter(encounter, command.target_id)
                pairs = set(encounter.close_pairs)
                if isinstance(encounter.spatial, BasicSpatialContext):
                    pairs.add(
                        (
                            min(actor.actor_id, target.actor_id),
                            max(actor.actor_id, target.actor_id),
                        )
                    )
                    encounter = move_basic(
                        encounter,
                        actor_id=actor.actor_id,
                        reference_actor_id=target.actor_id,
                        direction="approach",
                        yards=1,
                        command_id=command.id,
                        revision=state.revision,
                    ).model_copy(update={"close_pairs": tuple(sorted(pairs))})
                else:
                    actor = actor.model_copy(update={"position": target.position})
                    pairs.update(
                        (min(actor.actor_id, p.actor_id), max(actor.actor_id, p.actor_id))
                        for p in encounter.participants
                        if p.actor_id != actor.actor_id and p.position == target.position
                    )
                    encounter = CombatEngine._replace(encounter, actor).model_copy(
                        update={"close_pairs": tuple(sorted(pairs))}
                    )
            allowed_defenses: list[str] = ["none"]
            for choice in ("dodge", "parry"):
                try:
                    unarmed_defense(
                        runtime,
                        state,
                        encounter,
                        command.target_id,
                        choice,
                        None,
                        attacker_id=actor.actor_id,
                        location=command.location,
                    )
                except ValidationError:
                    if choice != "parry":
                        continue
                    candidates = (
                        (i.id, m.id)
                        for i in state.resources.items
                        if i.id in fighter(encounter, command.target_id).ready_item_ids
                        for e in catalog(runtime).entries
                        if e.definition_id == i.definition_id
                        for m in e.modes
                    )
                    for item, selected_mode in candidates:
                        try:
                            unarmed_defense(
                                runtime,
                                state,
                                encounter,
                                command.target_id,
                                choice,
                                item,
                                attacker_id=actor.actor_id,
                                location=command.location,
                                mode_id=selected_mode,
                            )
                        except ValidationError:
                            continue
                        break
                    else:
                        continue
                allowed_defenses.insert(0, choice)
            pending = PendingUnarmed.model_validate(
                {
                    "id": "unarmed:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    "actor_id": actor.actor_id,
                    "target_id": command.target_id,
                    "action": command.action,
                    "grip_id": command.grip_id,
                    "skill": command.skill,
                    "foot": command.foot,
                    "hands": command.hands,
                    "location": command.location,
                    "allowed": tuple(allowed_defenses),
                }
            )
            encounter = encounter.model_copy(update={"pending_unarmed": pending})
            return (
                state,
                encounter,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.unarmed_defense_required",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    pending_defense_id=pending.id,
                    available=pending.allowed,
                ),
            )
        state, encounter, trace = control(runtime, state, encounter, command)
    if not reacting:
        state = injury_turn(
            runtime, state, trace.actor_id, command.id, start=False, do_nothing=False
        )
    encounter = settle_control(state, encounter)
    encounter = encounter.model_copy(
        update={
            "pending_unarmed": None,
            "unarmed_history": encounter.unarmed_history + (trace,),
            "blocked_reason": trace.blocked_reason,
        }
    )
    if not trace.blocked_reason:
        encounter = CombatEngine._advance(encounter)
    return (
        state,
        encounter,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.unarmed_resolved",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            unarmed=trace,
        ),
    )
