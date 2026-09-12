"""Joining, leaving and withdrawing from an encounter."""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    BasicJoinPlacement,
    EndEncounter,
    HexJoinPlacement,
    JoinEncounter,
    SquareJoinPlacement,
    TypedCombatCommand,
    WithdrawEncounter,
)
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    CombatResult,
    CombatWithdrawal,
    Encounter,
)
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    BasicSpatialFact,
    HexSpatialContext,
)
from wayfarer.engine.simulation.combat.withdrawal import (
    require_basic_escape,
    require_hex_escape,
    require_resolved_boundary,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep
from wayfarer.orchestration.play import PlayService


def _join(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    assert isinstance(command, JoinEncounter)
    from wayfarer.engine.simulation.campaign.party import group_for

    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Reinforcements join between resolved combat stages")
    joining_actor_id = command.joining_actor_id or command.actor_id
    if joining_actor_id in encounter.turn_order:
        raise ConflictError("Actor already participates")
    prior_withdrawal = next(
        (
            entry
            for entry in reversed(encounter.withdrawals)
            if entry.actor.actor_id == joining_actor_id
        ),
        None,
    )
    if prior_withdrawal is not None and encounter.turn_index != 0:
        raise ConflictError("Returning combatants rejoin only at a round boundary")
    joining_actor = next((a for a in state.actors if a.actor_id == joining_actor_id), None)
    if (
        joining_actor is None
        or joining_actor.conditions
        or joining_actor.available_at > resources.game_time
    ):
        raise ValidationError("Reinforcement joining_actor is unavailable")
    if next(p.current for p in resources.pools if p.id == f"hp:{joining_actor.actor_id}") == 0:
        raise ValidationError("Reinforcement joining_actor is incapacitated")
    source = group_for(state, joining_actor_id)
    target_group = group_for(state, encounter.current_actor_id)
    if source.scene_id != target_group.scene_id or source.paused or target_group.paused:
        raise ValidationError("Reinforcements must be in the encounter scene")
    if (
        source.ready_through != resources.game_time
        or target_group.ready_through != resources.game_time
        or any(q.group_id in (source.id, target_group.id) for q in state.party.queue)
    ):
        raise ConflictError("Reinforcement arrival requires synchronized time")
    placement = command.placement
    if placement is None and command.position is not None:
        placement = SquareJoinPlacement(position=command.position, facing=command.facing)
    if placement is None or placement.kind != encounter.spatial_kind:
        raise ValidationError("Reinforcement placement must match the encounter representation")
    basic_facts: tuple[BasicSpatialFact, ...] = ()
    if isinstance(placement, BasicJoinPlacement):
        if command.joining_actor_id is None:
            raise ValidationError("Basic reinforcement placement requires GM admission")
        basic_facts = placement.facts
        if any(
            fact.provenance.source != "gm-adjudication"
            or fact.provenance.source_id != command.id
            or fact.provenance.declared_by != command.actor_id
            or fact.provenance.declared_revision != command.expected_revision
            or fact.provenance.invalidated_revision is not None
            or joining_actor_id not in (fact.subject_id, fact.object_id)
            or fact.subject_id == fact.object_id
            or not {fact.subject_id, fact.object_id}
            <= set(encounter.turn_order + (joining_actor_id,))
            for fact in basic_facts
        ):
            raise ValidationError("Basic reinforcement facts require scoped GM provenance")
        keys = {
            (
                fact.kind,
                min(fact.subject_id, fact.object_id)
                if fact.kind == "distance"
                else fact.subject_id,
                max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
            )
            for fact in basic_facts
        }
        required = {
            (kind, subject, object_id)
            for other in encounter.turn_order
            for kind, subject, object_id in (
                ("distance", min(joining_actor_id, other), max(joining_actor_id, other)),
                *(
                    (kind, left, right)
                    for kind in ("reach", "visibility", "cover", "obstacle", "retreat")
                    for left, right in ((joining_actor_id, other), (other, joining_actor_id))
                ),
            )
        }
        if keys != required or len(keys) != len(basic_facts):
            raise ValidationError(
                "Basic reinforcement placement requires one complete fact set per combatant"
            )
    if prior_withdrawal is None:
        build, _ = play.engine.reviewer.activate(
            joining_actor.proposal,
            joining_actor.approval,
            campaign_id=state.campaign_id,
            actor_id=joining_actor.actor_id,
        )
        initiative = int(next(v.value for v in build.sheet.values if v.target == "attribute:dx"))
        participant = Combatant(
            actor_id=joining_actor.actor_id,
            initiative=initiative,
            position=placement.position
            if isinstance(placement, (SquareJoinPlacement, HexJoinPlacement))
            else None,
            facing=placement.facing if isinstance(placement, SquareJoinPlacement) else "north",
            hex_facing=placement.facing if isinstance(placement, HexJoinPlacement) else None,
            reach=engine.rules.default_reach,
            movement_allowance=engine.rules.movement_allowance,
            ready_item_ids=tuple(
                sorted(
                    i.id
                    for i in resources.items
                    if i.owner_id == joining_actor.actor_id and i.equipped and i.ready
                )
            ),
        )
    else:
        participant = prior_withdrawal.actor.model_copy(
            update={
                "position": placement.position
                if isinstance(placement, (SquareJoinPlacement, HexJoinPlacement))
                else None,
                "facing": placement.facing
                if isinstance(placement, SquareJoinPlacement)
                else prior_withdrawal.actor.facing,
                "hex_facing": placement.facing if isinstance(placement, HexJoinPlacement) else None,
            }
        )
    if isinstance(encounter.spatial, BasicSpatialContext):
        encounter = encounter.model_copy(
            update={
                "spatial_context": encounter.spatial.model_copy(
                    update={"facts": encounter.spatial.facts + basic_facts}
                )
            }
        )
    encounter = encounter.add_participant(participant)
    joined_participants = encounter.participants
    order = tuple(
        p.actor_id for p in sorted(joined_participants, key=lambda p: (-p.initiative, p.actor_id))
    )
    current_actor = encounter.current_actor_id
    encounter = encounter.model_copy(
        update={
            "turn_order": order,
            "turn_index": order.index(current_actor),
        }
    )
    if source.id != target_group.id:
        remaining = tuple(a for a in source.actor_ids if a != joining_actor.actor_id)
        groups = tuple(
            g.model_copy(
                update={
                    "actor_ids": g.actor_ids + (joining_actor.actor_id,),
                    "generation": g.generation + 1,
                }
            )
            if g.id == target_group.id
            else g.model_copy(update={"actor_ids": remaining, "generation": g.generation + 1})
            if g.id == source.id
            else g
            for g in state.party.groups
            if g.id != source.id or remaining
        )
        state = state.model_copy(
            update={"party": state.party.model_copy(update={"groups": groups})}
        )
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    if isinstance(placement, HexJoinPlacement):
        from wayfarer.engine.simulation.combat.tactical import sight

        joined = next(p for p in encounter.participants if p.actor_id == joining_actor_id)
        board = play.rules_context.require_hex(encounter)
        if not any(
            sight(encounter, observer, joined, board=board)
            for observer in encounter.participants
            if observer.actor_id != joining_actor_id
        ):
            raise ValidationError("Hex reinforcement must arrive in visible placement")
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.reinforcement_arrived",
        round=encounter.round,
        current_actor_id=current_actor,
    )
    return CombatStep(state, encounter, resources, result)


def _end(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    resources = state.resources
    assert isinstance(command, EndEncounter)
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Encounter cannot end during a pending defense")
    encounter = encounter.model_copy(
        update={"status": "completed", "completion_reason": command.reason}
    )
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.completed",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _withdraw(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    """Finalize a resolved flight without granting another movement or action."""
    assert isinstance(command, WithdrawEncounter)
    actor = require_resolved_boundary(encounter, command.actor_id)
    others = tuple(p for p in encounter.participants if p.actor_id != command.actor_id)
    spatial = encounter.spatial
    if isinstance(spatial, BasicSpatialContext):
        require_basic_escape(spatial, command.actor_id, others)
        spatial = spatial.model_copy(
            update={
                "facts": tuple(
                    fact
                    for fact in spatial.facts
                    if command.actor_id not in (fact.subject_id, fact.object_id)
                )
            }
        )
    elif isinstance(spatial, HexSpatialContext):
        board = context.play.rules_context.require_hex(encounter)
        require_hex_escape(encounter, actor, others, board=board)
        spatial = spatial.model_copy(
            update={
                "placements": tuple(
                    placement
                    for placement in spatial.placements
                    if placement.actor_id != command.actor_id
                )
            }
        )
    else:
        raise ValidationError("Square withdrawal needs explicit adjudication")

    from wayfarer.engine.simulation.campaign.party import Subgroup, group_for

    if not state.party.groups:
        raise ValidationError("Withdrawal requires shared-time party state")
    group = group_for(state, command.actor_id)
    if len(group.actor_ids) < 2 or any(g.id == command.new_group_id for g in state.party.groups):
        raise ValidationError("Withdrawal requires a fresh independent subgroup")
    new_group = Subgroup(
        id=command.new_group_id,
        scene_id=group.scene_id,
        actor_ids=(command.actor_id,),
        ready_through=group.ready_through,
    )
    groups = tuple(
        g.model_copy(
            update={
                "actor_ids": tuple(a for a in g.actor_ids if a != command.actor_id),
                "generation": g.generation + 1,
            }
        )
        if g.id == group.id
        else g
        for g in state.party.groups
    ) + (new_group,)
    state = state.model_copy(update={"party": state.party.model_copy(update={"groups": groups})})

    removed_index = encounter.turn_order.index(command.actor_id)
    order = tuple(a for a in encounter.turn_order if a != command.actor_id)
    round_number = encounter.round
    if removed_index == encounter.turn_index:
        if removed_index == len(order):
            turn_index = 0
            round_number += 1
        else:
            turn_index = removed_index
    else:
        turn_index = encounter.turn_index - int(removed_index < encounter.turn_index)
    completed = len(order) == 1
    encounter = encounter.model_copy(
        update={
            "participants": others,
            "turn_order": order,
            "turn_index": turn_index,
            "round": round_number,
            "spatial_context": spatial,
            "status": "completed" if completed else "active",
            "completion_reason": "withdrawal" if completed else None,
            "close_pairs": tuple(
                pair for pair in encounter.close_pairs if command.actor_id not in pair
            ),
            "ranged_situations": tuple(
                situation
                for situation in encounter.ranged_situations
                if command.actor_id not in (situation.attacker_id, situation.defender_id)
            ),
            "withdrawals": encounter.withdrawals
            + (
                CombatWithdrawal(
                    actor=actor,
                    round=encounter.round,
                    turn_index=removed_index,
                    group_id=command.new_group_id,
                ),
            ),
        }
    )
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.withdrawn",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
        ),
    )


def preview_withdrawal(
    play: PlayService, state: PlayState, encounter: Encounter, command: WithdrawEncounter
) -> None:
    """Validate a withdrawal choice without committing state or advancing clocks."""
    _withdraw(state, command, encounter, CombatContext(play, state))
