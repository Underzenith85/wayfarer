"""Opted-in ordinary battle transitions, shared by native hosts and seed replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from wayfarer.engine.rules.types.battle_fatigue import BattleFatiguePolicy
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.commands import (
    JoinEncounter,
    StartBasicEncounter,
    StartEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
    TypedCombatCommand,
    WithdrawEncounter,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.withdrawal import elapsed_seconds
from wayfarer.engine.simulation.equipment.catalog import inventory_load
from wayfarer.engine.simulation.health.battle_fatigue import (
    BattleFatigueLedger,
    identifier,
    read,
    write,
)
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue, exertion_cost
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext


def policy(context: CombatContext) -> BattleFatiguePolicy | None:
    return context.play.engine.reviewer.compiler.battle_fatigue


def binding(context: CombatContext) -> str:
    compiler = context.play.engine.reviewer.compiler
    selected = policy(context)
    assert selected is not None
    data = {"policy": asdict(selected), "rules": asdict(compiler.rules)}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def preflight(state: PlayState, command: TypedCombatCommand, context: CombatContext) -> None:
    selected = policy(context)
    if selected is None:
        return
    if isinstance(command, (TakeUnarmedTurn, JoinEncounter, WithdrawEncounter)):
        raise ValidationError("Battle fatigue roster changes and unarmed combat are outside scope")
    if isinstance(command, (StartEncounter, StartBasicEncounter)):
        if any(e.id == identifier(command.encounter_id) for e in state.resources.events):
            raise ValidationError("Battle fatigue encounter identity was already used")
        return
    encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
    if encounter is None:
        raise ValidationError("Unknown battle fatigue encounter")
    ledger = read(state.resources, encounter.id)
    if (
        ledger.binding != binding(context)
        or ledger.roster != encounter.turn_order
        or (ledger.round, ledger.index) != (encounter.round, encounter.turn_index)
        or ledger.charged != (encounter.status == "completed")
        or ledger.seconds > selected.maximum_seconds
    ):
        raise ValidationError("Battle fatigue ledger differs from its exact encounter and policy")
    if (
        isinstance(command, TakeCombatTurn)
        and ledger.seconds >= selected.maximum_seconds
        and encounter.pending_defense is None
    ):
        raise ValidationError("End this battle before starting another combat turn")


def finish(
    state: PlayState,
    command: TypedCombatCommand,
    encounter: Encounter,
    result: CombatResult,
    context: CombatContext,
) -> PlayState:
    selected = policy(context)
    if selected is None:
        return state
    prior = next((e for e in context.initial_state.encounters if e.id == encounter.id), None)
    if isinstance(command, (StartEncounter, StartBasicEncounter)):
        ledger = BattleFatigueLedger(
            encounter_id=encounter.id,
            binding=binding(context),
            initial_round=encounter.round,
            initial_index=encounter.turn_index,
            round=encounter.round,
            index=encounter.turn_index,
            seconds=0,
            roster=encounter.turn_order,
        )
    else:
        ledger = read(state.resources, encounter.id)
    participants = set(ledger.rolled)
    # These traces exist only after real attack dice. Pending selection alone is
    # deliberately insufficient; an unrolled pending attack remains exempt.
    for candidate in (prior, encounter):
        pending = candidate.pending_defense if candidate is not None else None
        if pending is not None and pending.attack_roll is not None:
            participants.add(pending.attacker_id)
    attacker: str | None
    defender: str | None
    injury = result.injury
    if injury is not None:
        pending = prior.pending_defense if prior is not None else None
        if pending is not None:
            attacker, defender = pending.attacker_id, pending.defender_id
        elif isinstance(command, TakeCombatTurn) and command.target_id is not None:
            attacker, defender = command.actor_id, command.target_id
        else:
            # A critical continuation may repeat its preserved injury trace; its
            # original rolled actors have already been captured. Never infer IDs
            # from wounds or a selected defense.
            attacker = defender = None
            if not participants:
                raise ValidationError("Battle fatigue cannot identify the actual rolled attack")
        if attacker is not None:
            participants.add(attacker)
        if defender is not None and (
            injury.defense is not None or injury.second_defense is not None
        ):
            participants.add(defender)
    completed = encounter.status == "completed"
    partial_second = completed and encounter.turn_index > ledger.initial_index
    seconds = ledger.seconds + elapsed_seconds(prior, encounter)
    # B362-363 actor turns overlap. A turn already completed in an open
    # final cycle belongs to one battle second even when GM End itself takes
    # zero world time. Canonical elapsed_seconds already includes this second
    # when the completing command changes the turn index; never count it twice.
    if partial_second and seconds == encounter.round - ledger.initial_round:
        seconds += 1
    if seconds > selected.maximum_seconds:
        raise ValidationError("Battle exceeds the supported ordinary battle duration")
    resources = state.resources
    if completed and not ledger.charged:
        for actor_id in sorted(participants):
            build = context.play.rules_context.approved_build(state, actor_id)
            stats = build.statistics
            if stats is None:
                raise ValidationError("Battle fatigue requires approved GURPS statistics")
            load = inventory_load(
                catalog(context.play.rules_context),
                context.play.engine.resources,
                resources,
                actor_id,
                stats,
            )
            if load.level is None:
                raise ValidationError("Battle fatigue requires supported encumbrance")
            amount = exertion_cost("battle", seconds=seconds, encumbrance=int(load.level))
            if amount:
                resources, _ = apply_fatigue(
                    resources,
                    FatigueCost(
                        id="battle-cost:"
                        + hashlib.sha256(
                            json.dumps([encounter.id, ledger.binding, actor_id]).encode()
                        ).hexdigest(),
                        actor_id=actor_id,
                        expected_revision=resources.revision,
                        amount=amount,
                    ),
                    ht=stats.ht,
                    rng=context.play.rng,
                    system=True,
                )
    ledger = BattleFatigueLedger.model_validate(
        ledger.model_copy(
            update={
                "round": encounter.round,
                "index": encounter.turn_index,
                "seconds": seconds,
                "rolled": tuple(sorted(participants)),
                "charged": completed,
                "partial_second": partial_second,
            }
        )
    )
    return state.model_copy(update={"resources": write(resources, ledger)})
