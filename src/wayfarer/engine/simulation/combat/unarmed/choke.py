"""Chokes, from the first grip to the hazard they leave behind."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.types.hazard import CombatHazardTurn, HazardSchedule, HazardSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.unarmed.records import Grip, require_basic
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import ResolveChokeEffects
    from wayfarer.engine.simulation.rules_context import RulesContext


def start_choke(
    runtime: RulesContext,
    state: PlayState,
    grip: Grip,
    command_id: str,
    *,
    encounter: Encounter | None = None,
) -> tuple[PlayState, Grip]:
    """The existing suffocation schedule owns FP, consciousness and death timing."""

    if grip.choke_hold and encounter is None:
        raise ValidationError("Choke Hold requires its holder-turn timing context")
    compiled = build(runtime, state, grip.target_id)
    assert compiled.statistics is not None
    entity = next(e for e in state.world.entities if e.id == grip.target_id)
    schedule = HazardSchedule(
        id="grip-air:" + hashlib.sha256(grip.id.encode()).hexdigest(),
        actor_id=grip.target_id,
        spec=HazardSpec(
            id=grip.id,
            kind="suffocation",
            scene_id=entity.location_id or "unknown",
            delay=1,
            interval=1,
            cycles=240,
            resistible=False,
            reference="B404/B436" if grip.choke_hold else "B370/B436",
        ),
        started=state.resources.game_time,
        due=state.resources.game_time + 1,
        remaining=240,
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        swimming=compiled.statistics.ht,
        no_air_since=state.resources.game_time,
        combat_turn=CombatHazardTurn(
            encounter_id=encounter.id,
            actor_id=grip.holder_id,
            round=encounter.round + 1,
        )
        if grip.choke_hold and encounter is not None
        else None,
    )
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id="choke-start:" + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=grip.target_id,
            expected_revision=state.resources.revision,
            kind="enter",
            hazard_id=grip.id,
        ),
        schedule,
        rng=runtime.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources}), grip.model_copy(
        update={"hazard_id": schedule.id}
    )


def start_choke_hold(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    grip: Grip,
    command_id: str,
) -> tuple[PlayState, Grip]:
    """Start only the specialized B404 exposure; ordinary grips pass through."""

    if not grip.choke_hold:
        return state, grip
    return start_choke(runtime, state, grip, command_id, encounter=encounter)


def resolve_choke(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ResolveChokeEffects
) -> tuple[PlayState, CombatResult]:

    require_basic(catalog(runtime).profile_id)
    validate_choke_resolution(state, encounter, command)
    grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
    assert grip is not None
    schedule = next(h for h in state.resources.hazards if h.id == grip.hazard_id)
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id="choke-tick:" + hashlib.sha256(command.id.encode()).hexdigest(),
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            kind="resolve",
            hazard_id=grip.id,
        ),
        schedule,
        rng=runtime.rng,
        system=True,
        combat_turn=schedule.combat_turn,
    )
    state = state.model_copy(update={"resources": resources})
    if not fatigue_ready(state, command.actor_id):
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == command.actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    return state, CombatResult(
        encounter_id=encounter.id,
        code="combat.choke_effects_resolved",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )


def require_choke_turn_settled(
    state: PlayState, encounter: Encounter, *, exempt: bool = False
) -> None:
    """B404 deadlines follow the holder's turn, not the start of a round."""

    if exempt or encounter.status != "active" or encounter.wait_interrupt is not None:
        return
    if any(
        hazard.active
        and hazard.combat_turn is not None
        and hazard.combat_turn.encounter_id == encounter.id
        and hazard.combat_turn.actor_id == encounter.current_actor_id
        and hazard.combat_turn.round <= encounter.round
        and hazard.due <= state.resources.game_time
        for hazard in state.resources.hazards
    ):
        raise ConflictError("Resolve Choke Hold suffocation before the holder's turn")


def finish_choke_turns(state: PlayState, encounter: Encounter) -> PlayState:
    """Combat ending does not restore air: surviving holds become clock exposures."""

    if encounter.status == "active":
        return state
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "hazards": tuple(
                        hazard.model_copy(
                            update={"combat_turn": None, "due": state.resources.game_time + 1}
                        )
                        if hazard.combat_turn is not None
                        and hazard.combat_turn.encounter_id == encounter.id
                        else hazard
                        for hazard in state.resources.hazards
                    )
                }
            )
        }
    )


def validate_choke_resolution(
    state: PlayState, encounter: Encounter, command: ResolveChokeEffects
) -> None:
    """Pure validation shared with future command-choice projections."""

    grip = next((g for g in encounter.grips if g.id == command.grip_id), None)
    if grip is None or grip.target_id != command.actor_id or grip.hazard_id is None:
        raise ValidationError("Only the choking actor may settle this grip's due effects")
    schedule = next(h for h in state.resources.hazards if h.id == grip.hazard_id)
    if schedule.combat_turn is not None and (
        encounter.status != "active"
        or encounter.wait_interrupt is not None
        or encounter.pending_unarmed is not None
        or encounter.pending_defense is not None
        or encounter.current_actor_id != schedule.combat_turn.actor_id
        or encounter.round != schedule.combat_turn.round
    ):
        raise ValidationError("Choke Hold suffocation is due on the holder's following turn")
    if not schedule.active or schedule.due > state.resources.game_time:
        raise ValidationError("This grip has no due suffocation effects")


def retire_chokes(
    runtime: RulesContext,
    state: PlayState,
    before: tuple[Grip, ...],
    after: tuple[Grip, ...],
    command_id: str,
) -> PlayState:

    remaining = {g.id for g in after}
    for grip in before:
        if grip.id in remaining or grip.hazard_id is None:
            continue
        schedule = next(h for h in state.resources.hazards if h.id == grip.hazard_id)
        resources, _ = apply_hazard(
            state.resources,
            HazardCommand(
                id="choke-end:" + hashlib.sha256((command_id + grip.id).encode()).hexdigest(),
                actor_id=grip.target_id,
                expected_revision=state.resources.revision,
                kind="leave",
                hazard_id=grip.id,
            ),
            schedule,
            rng=runtime.rng,
            system=True,
        )
        state = state.model_copy(update={"resources": resources})
    return state
