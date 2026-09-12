"""Drifting, sinking, drowning and pressure."""

from fractions import Fraction
from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    ResolveWaterAftermath,
)
from wayfarer.engine.simulation.movement.vehicles.motion import (
    footprint,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError


def water_hazard(
    command_id: str,
    state: ResourceState,
    board: HexBattlefield,
    facts: WaterOccupantCheck,
    kind: Literal["drowning", "suffocation", "pressure"],
) -> HazardSchedule:
    """Bind a water casualty to the shared environmental clock."""
    stage: Literal["struggling", "cycles", "exposure"]
    if kind == "drowning":
        interval, cycles, damage, resistible, stage = 5, 100000, 0, True, "struggling"
    elif kind == "suffocation":
        interval, cycles, damage, resistible, stage = 1, 240, 0, False, "cycles"
    elif kind == "pressure":
        interval, cycles, damage, resistible, stage = 1, 100000, 1, True, "exposure"
    else:
        raise ValidationError("Unknown water casualty")
    return HazardSchedule(
        id=internal_id(command_id, kind + ":" + facts.actor_id),
        actor_id=facts.actor_id,
        spec=HazardSpec(
            id="vehicle-" + kind,
            kind=kind,
            scene_id=board.id,
            delay=1 if kind == "suffocation" else 0,
            interval=interval,
            cycles=cycles,
            damage_dice=damage,
            damage_add=0 if damage else 1,
            resistible=resistible,
            reference="Basic Set Campaigns B435-437, B469",
        ),
        started=state.game_time,
        due=state.game_time + interval,
        remaining=cycles,
        ht=facts.ht,
        will=facts.will,
        swimming=facts.swimming,
        stage=stage,
        no_air_since=state.game_time if kind == "suffocation" else None,
        next_check_at=state.game_time + 5 if kind == "drowning" else None,
    )


def resolve(operation: Operation) -> Outcome:
    engine = operation.engine
    state = operation.state
    command = operation.command
    assert isinstance(command, ResolveWaterAftermath)
    t = operation.transport
    board = operation.board
    occupied = operation.occupied
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    if t.locomotion not in ("water", "underwater"):
        raise ValidationError("Water aftermath requires a water vehicle")
    if board is None or board.profile_id != t.profile_id:
        raise ValidationError("Water aftermath requires the matching tactical map")
    if command.action == "drift":
        if (
            t.status != "drifting"
            or command.direction is None
            or not command.distance
            or command.waterline is None
            or command.skill is not None
            or command.leak_damage
        ):
            raise ValidationError("Current drift requires direction, distance, and waterline")
        point = Hex(q=t.q, r=t.r)
        for _ in range(command.distance):
            point = neighbor(point, command.direction)
            pose = t.model_copy(update={"q": point.q, "r": point.r})
            for cell in footprint(pose):
                terrain = board.cell(cell)
                if cell in occupied:
                    raise ValidationError("Water current meets an undeclared collision")
                if t.locomotion == "water":
                    if command.waterline - terrain.ground < Fraction(
                        t.draft * 12 + t.draft_inches, 12
                    ):
                        raise ValidationError("Water current grounds the vehicle")
                elif not terrain.ground < t.altitude < command.waterline:
                    raise ValidationError("Underwater current meets surface or bottom")
        affected = (
            t.model_copy(
                update={
                    "q": point.q,
                    "r": point.r,
                    "status": "controlled",
                    "aftermath_turn": state.game_time,
                }
            ),
        )
    elif command.action == "right":
        if (
            t.status != "capsized"
            or not t.unsinkable
            or t.operator_id not in t.occupants
            or command.skill is None
            or command.direction is not None
            or command.distance
            or command.leak_damage
        ):
            raise ValidationError("Only an unsinkable capsized craft can be righted")
        if t.recovery_turn == state.game_time:
            raise ConflictError("Capsize recovery already attempted this second")
        check = success_roll(
            t.profile_id,
            command.skill + t.handling,
            check_modifiers(state, command.actor_id, "dx"),
            rng=rng,
        )
        affected = (
            t.model_copy(
                update={
                    "status": "controlled" if check.outcome.succeeded else "capsized",
                    "recovery_turn": state.game_time,
                    "traces": (
                        *t.traces,
                        VehicleTrace(
                            command_id=command.id,
                            reason="capsize-recovery",
                            actor_id=command.actor_id,
                            dice=check.dice,
                            target=check.effective_target,
                            margin=check.margin,
                        ),
                    ),
                }
            ),
        )
    elif command.action == "sink":
        if t.status != "sinking" or command.waterline is None or command.skill is not None:
            raise ValidationError("Only a sinking craft accepts a sinking tick")
        if t.aftermath_turn == state.game_time:
            raise ConflictError("Sinking already advanced this second")
        facts = {f.actor_id: f for f in command.occupants}
        if set(facts) != set(t.occupants):
            raise ValidationError("Sinking requires compiled facts for every occupant")
        submersion = t.submersion + t.sink_rate + t.leak_rate
        bottom = min(board.cell(cell).ground for cell in footprint(t))
        depth = command.waterline - bottom
        kind: Literal["drowning", "suffocation"] = "drowning" if t.open_cabin else "suffocation"
        existing = {h.actor_id for h in state.hazards if h.active}
        state = state.model_copy(
            update={
                "hazards": (
                    *state.hazards,
                    *(
                        water_hazard(command.id, state, board, facts[a], kind)
                        for a in t.occupants
                        if a not in existing
                    ),
                )
            }
        )
        affected = (
            t.model_copy(
                update={
                    "submersion": submersion,
                    "status": "crashed" if submersion >= depth else "sinking",
                    "aftermath_turn": state.game_time,
                }
            ),
        )
    else:
        if (
            command.action != "stress-leak"
            or t.locomotion != "underwater"
            or t.status != "stress-failure"
            or not command.leak_damage
            or command.skill is not None
        ):
            raise ValidationError("Underwater stress requires an authored leak outcome")
        facts = {f.actor_id: f for f in command.occupants}
        if set(facts) != set(t.occupants):
            raise ValidationError("Pressure loss requires compiled occupant facts")
        state, _ = apply_object(
            engine,
            state,
            DamageObject(
                id=internal_id(command.id, "hull"),
                actor_id=command.actor_id,
                expected_revision=state.revision,
                item_id=t.body_id,
                basic_damage=command.leak_damage,
                damage_type="cr",
            ),
            system=True,
            rng=rng,
        )
        existing = {h.actor_id for h in state.hazards if h.active}
        state = state.model_copy(
            update={
                "hazards": (
                    *state.hazards,
                    *(
                        water_hazard(command.id, state, board, facts[a], "pressure")
                        for a in t.occupants
                        if a not in existing
                    ),
                )
            }
        )
        affected = (
            t.model_copy(update={"status": "sinking", "leak_rate": max(1, command.leak_damage)}),
        )
    return state, affected
