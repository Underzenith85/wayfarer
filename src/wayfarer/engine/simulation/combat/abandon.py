"""Private B363-366 continuation: forgo an unresolved attack, never undo a roll.

This finishes the already-spent maneuver. Its restrictions and paid effects last
normally; neither a new maneuver nor a replacement target is selected.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import injury_turn
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter, PendingDefense
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "combat-abandoned-attack:"


class AbandonPendingAttack(Command):
    operation: Literal["abandon-pending-attack"] = "abandon-pending-attack"
    encounter_id: Id
    pending_id: Id


class AbandonedAttack(Record):
    kind: Literal["abandoned-attack"] = "abandoned-attack"
    command_id: Id
    encounter_id: Id
    actor_id: Id
    round: int = Field(ge=1)
    pending_defense: PendingDefense | None = None
    pending_unarmed: PendingUnarmed | None = None
    spent_maneuver: ManeuverState


def history(state: PlayState) -> tuple[AbandonedAttack, ...]:
    return tuple(
        AbandonedAttack.model_validate_json(event.kind)
        for event in state.resources.events
        if event.id.startswith(PREFIX)
    )


def abandon(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: AbandonPendingAttack,
) -> tuple[PlayState, Encounter, CombatResult]:
    engine = runtime.combat
    if not isinstance(engine, CombatEngine):
        raise ValidationError("Campaign combat is not configured")
    if state.lifecycle != "active":
        raise ValidationError("Resume the campaign before acting")
    if command.expected_revision != state.revision:
        raise ConflictError("Play revision changed")
    if encounter.status != "active" or encounter.blocked_reason is not None:
        raise ConflictError("Abandonment requires an active, unblocked pending attack")
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    if (pending is None) == (unarmed is None):
        raise ConflictError("Encounter must have exactly one unresolved pending attack")
    if pending is not None:
        identifier, attacker_id, target_id = pending.id, pending.attacker_id, pending.defender_id
    else:
        assert unarmed is not None
        identifier, attacker_id, target_id = unarmed.id, unarmed.actor_id, unarmed.target_id
    if command.pending_id != identifier:
        raise ConflictError("Pending attack changed")
    if command.actor_id != attacker_id:
        raise ValidationError("Only the pending attacker may abandon the remaining attack")
    if pending is not None and pending.attack_roll is not None:
        raise ConflictError("A committed attack roll cannot be abandoned; resolve its defense")
    if pending is not None and pending.suppression_zone_id is not None:
        raise ConflictError("Paid suppression fire must resolve its queued defense")
    if encounter.current_actor_id != attacker_id:
        raise ConflictError("The pending attacker does not own this turn")
    interrupt = encounter.wait_interrupt
    if interrupt is not None and (not interrupt.reacting or interrupt.waiter_id != attacker_id):
        raise ConflictError("Resolve the interrupted Wait before abandoning this attack")
    actor = next(p for p in encounter.participants if p.actor_id == attacker_id)
    if actor.high_speed is not None and actor.high_speed.remaining_yards:
        raise ConflictError("Cancelling high-speed movement requires the B395 braking rules")

    # Reuse ordinary recovery/time boundaries, without requiring a fresh physical
    # action or repeating start-of-turn consciousness, exertion or equipment use.
    guard(state, attacker_id, "take_combat_turn", allow_fright=True)
    affected = frozenset({attacker_id, target_id, *(g.target_id for g in encounter.grips)})
    require_settled(state.resources.recovery_tasks, affected, state.resources.game_time)
    require_hazards_settled(state.resources.hazards, affected, state.resources.game_time)
    record = AbandonedAttack(
        command_id=command.id,
        encounter_id=encounter.id,
        actor_id=attacker_id,
        round=encounter.round,
        pending_defense=pending,
        pending_unarmed=unarmed,
        spent_maneuver=actor.maneuver_state,
    )
    event = ResourceEvent(
        id=PREFIX + hashlib.sha256(command.id.encode()).hexdigest(),
        at=state.resources.game_time,
        target_id=attacker_id,
        kind=record.model_dump_json(),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    )
    if engine.rules.gurps_equipment is not None and interrupt is None:
        state = injury_turn(runtime, state, attacker_id, command.id, start=False, do_nothing=False)
    spent = actor.maneuver_state.model_copy(
        update={
            "attacks_remaining": 0,
            "second_unarmed_attack": None,
            "second_attack_item_id": None,
            "second_attack_target_id": None,
            "second_attack_mode_id": None,
        }
    ).consume_attack_setup()
    encounter = engine._replace(encounter, actor.model_copy(update={"maneuver_state": spent}))
    encounter = engine._advance(
        encounter.model_copy(update={"pending_defense": None, "pending_unarmed": None})
    )
    return (
        state,
        encounter,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.attack_abandoned",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=engine.available(encounter, encounter.current_actor_id),
        ),
    )
