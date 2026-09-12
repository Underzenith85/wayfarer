"""Settling shared time and finishing an encounter."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    TakeCombatTurn,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.withdrawal import elapsed_seconds
from wayfarer.engine.simulation.resources import Advance
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep


def _settle_combat(
    step: CombatStep,
    command: TypedCombatCommand,
    encounters: tuple[Encounter, ...],
    context: CombatContext,
) -> tuple[CombatStep, tuple[Encounter, ...]]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    state = step.state
    encounter = step.encounter
    resources = step.resources
    result = step.result
    if (
        engine.rules.gurps_equipment is not None
        and encounter.pending_defense is None
        and encounter.pending_unarmed is None
        and encounter.status == "active"
    ):
        from wayfarer.engine.simulation.actors import fatigue_ready

        conscious = {
            p.id.removeprefix("hp:")
            for p in resources.pools
            if p.id.startswith("hp:")
            and p.injury is not None
            and not p.injury.incapacitated
            and fatigue_ready(
                state.model_copy(update={"resources": resources}), p.id.removeprefix("hp:")
            )
        }
        if len(conscious.intersection(encounter.turn_order)) < 2:
            encounter = encounter.model_copy(
                update={"status": "completed", "completion_reason": "incapacitation"}
            )
        else:
            while encounter.current_actor_id not in conscious:
                encounter = engine._advance(encounter)
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
        result = result.model_copy(
            update={
                "round": encounter.round,
                "current_actor_id": encounter.current_actor_id,
                "available": engine.available(encounter, encounter.current_actor_id),
            }
        )
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.unarmed.choke import retire_chokes
        from wayfarer.engine.simulation.combat.unarmed.fighters import settle_control

        prior_grips = next((e.grips for e in initial_state.encounters if e.id == encounter.id), ())
        encounter = settle_control(state.model_copy(update={"resources": resources}), encounter)
        state = retire_chokes(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            prior_grips,
            encounter.grips,
            command.id,
        )
        resources = state.resources
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
    if encounter.status == "completed" and engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import settle_crippling

        state = settle_crippling(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command.id,
        )
        resources = state.resources
    if engine.rules.gurps_equipment is not None:
        held = {i.id for i in resources.items if i.ready and i.equipped}
        hands = {
            p.actor_id: tuple((i, h) for i, h in p.hand_bindings if i in held)
            for p in encounter.participants
        }
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={
                            "hand_bindings": hands[p.actor_id],
                            "ready_item_ids": tuple(
                                sorted(
                                    i.id
                                    for i in resources.items
                                    if i.owner_id == p.actor_id and i.ready and i.equipped
                                )
                            ),
                        }
                    )
                    for p in encounter.participants
                )
            }
        )
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"held_item_hands": hands[a.actor_id]})
                    if a.actor_id in hands
                    else a
                    for a in state.actors
                )
            }
        )
    return replace(
        step, state=state, encounter=encounter, resources=resources, result=result
    ), encounters


def _finish_combat(
    step: CombatStep,
    command: TypedCombatCommand,
    encounters: tuple[Encounter, ...],
    context: CombatContext,
) -> tuple[PlayState, CombatResult]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    state = step.state
    encounter = step.encounter
    resources = step.resources
    result = step.result
    blast_deferred_ticks = step.blast_deferred_ticks
    party = state.party
    if party.groups:
        participants = set(encounter.turn_order)
        involved = tuple(g for g in party.groups if set(g.actor_ids) & participants)
        if len(involved) != 1 or not participants <= set(involved[0].actor_ids):
            raise ValidationError("Combat participants must share one subgroup")
        group = involved[0]
        if group.paused or any(q.group_id == group.id for q in party.queue):
            raise ConflictError("Combat subgroup is paused or has pending activity")
        if group.ready_through > resources.game_time:
            raise ConflictError("Combat waits at the shared-time barrier")
        prior = next((e for e in state.encounters if e.id == encounter.id), None)
        ticks = elapsed_seconds(prior, encounter) + blast_deferred_ticks
        from wayfarer.engine.simulation.combat.explosions import defer_round

        resources, ticks = defer_round(resources, encounter.id, ticks, command.id)
        party = party.model_copy(
            update={
                "groups": tuple(
                    g.model_copy(update={"ready_through": g.ready_through + ticks})
                    if g.id == group.id
                    else g
                    for g in party.groups
                )
            }
        )
    elif (
        engine.rules.attacks
        or engine.rules.gurps_equipment is not None
        or play.engine.rules.abilities
    ):
        prior = next((e for e in state.encounters if e.id == encounter.id), None)
        ticks = elapsed_seconds(prior, encounter) + blast_deferred_ticks
        from wayfarer.engine.simulation.combat.explosions import defer_round

        resources, ticks = defer_round(resources, encounter.id, ticks, command.id)
        if ticks:
            resources = play.engine.resources.apply(
                resources,
                Advance(
                    id="combat-time:" + hashlib.sha256(command.id.encode()).hexdigest()
                    if engine.rules.gurps_equipment
                    else f"{command.id}:round-time",
                    actor_id=encounter.current_actor_id,
                    expected_revision=resources.revision,
                    to=resources.game_time + ticks,
                ),
                system=True,
                rng=play.rng,
            )
    from wayfarer.engine.simulation.combat.ranged_readiness import interrupted_draws

    resources = interrupted_draws(
        play.rules_context, initial_state, resources, encounter.id, encounter
    )
    revision = state.revision + 1
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.combat.tactical import TacticalTrace

        checks: tuple[CheckTrace, ...] = (result.injury.attack,) if result.injury else ()
        if result.injury and result.injury.defense:
            checks += (result.injury.defense,)
        if result.unarmed:
            checks = result.unarmed.checks
        trace = TacticalTrace(
            command_id=command.id,
            actor_id=command.actor_id,
            code=result.code,
            totals=tuple(c.total for c in checks),
            targets=tuple(c.effective_target for c in checks),
            injury=result.injury.injury
            if result.injury
            else result.unarmed.injury
            if result.unarmed
            else 0,
        )
        encounter = encounter.model_copy(
            update={"tactical_traces": (encounter.tactical_traces + (trace,))[-50:]}
        )
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
    resources = resources.model_copy(update={"revision": revision})
    updated = state.model_copy(
        update={
            "revision": revision,
            "party": party,
            "resources": resources,
            "encounters": encounters,
            "last_combat_result": result,
            "rulings": expire_rulings(state.rulings, revision, resources.game_time),
        }
    )
    if updated.party.groups:
        from wayfarer.orchestration.party import PartyService

        updated = PartyService(play).flush(updated)
    if (
        isinstance(command, ChooseDefense)
        and engine.rules.attacks
        and encounter.status == "completed"
        and encounter.spatial_kind != "basic"
    ):
        previous = step.defense_before
        injury = result.injury
        assert previous is not None and injury is not None
        world = updated.world
        for consequence in engine.rules.consequences:
            if (
                consequence.battlefield_id
                in (
                    encounter.battlefield_id,
                    play.rules_context.require_hex(encounter).source_template_id
                    if encounter.spatial_kind == "hex"
                    else None,
                )
                and previous.pending_defense is not None
                and consequence.defeated_actor_id == previous.pending_defense.defender_id
                and injury.incapacitated
            ):
                for recipient in consequence.recipient_actor_ids:
                    for fact in consequence.fact_ids:
                        world = world.learn(recipient, fact)
        updated = updated.model_copy(update={"world": world})
    if isinstance(command, TakeCombatTurn):
        from wayfarer.engine.simulation.magic.area_fire import crossings

        updated = crossings(
            play.rules_context,
            updated,
            initial_state,
            command.actor_id,
            command.encounter_id,
            command.hex_path,
            command.id,
        )
    return updated, result
