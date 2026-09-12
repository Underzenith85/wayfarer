"""Damage to a vehicle and what it disables."""

from typing import Literal

from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.equipment.objects import DamageObject, StressObject, apply_object
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    DamageVehicle,
)
from wayfarer.engine.simulation.movement.vehicles.operations.context import Operation, Outcome
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


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
    assert isinstance(command, DamageVehicle)
    t = operation.transport
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    if t.locomotion == "ground-mount":
        raise ValidationError("Mounted creatures use the existing injury reducer")
    if (command.hit_location == "weapon") != (command.equipment_item_id is not None):
        raise ValidationError("Weapon hits require exactly one vehicle equipment item")
    operator_facts = command.operator_damage > 0 and command.operator_ht is not None
    if (command.operator_damage > 0) != (command.operator_ht is not None):
        raise ValidationError("Control hits require matching compiled operator injury facts")
    if command.hit_location != "controls" and operator_facts:
        raise ValidationError("Only control hits may include operator injury")
    item_id = command.equipment_item_id or t.body_id
    if command.equipment_item_id is not None:
        equipment = next((item for item in state.items if item.id == item_id), None)
        if equipment is None or equipment.owner_id not in t.occupants:
            raise ValidationError("Vehicle weapon must belong to one of its occupants")
    state, object_result = apply_object(
        engine,
        state,
        DamageObject(
            id=internal_id(command.id, "vehicle-hit"),
            actor_id=command.actor_id,
            expected_revision=state.revision,
            item_id=item_id,
            basic_damage=command.basic_damage,
            damage_type=command.damage_type,
        ),
        system=True,
        rng=rng,
    )
    if (
        item_id == t.body_id
        and object_result.condition.hp <= 0
        and not object_result.condition.disabled
    ):
        state, object_result = apply_object(
            engine,
            state,
            StressObject(
                id=internal_id(command.id, "vehicle-stress"),
                actor_id=command.actor_id,
                expected_revision=state.revision,
                item_id=item_id,
            ),
            system=True,
            rng=rng,
        )
    if command.operator_damage:
        assert command.operator_ht is not None
        state, _ = apply_injury(
            state,
            Wound(
                id=internal_id(command.id, "operator"),
                actor_id=t.operator_id,
                expected_revision=state.revision,
                basic_damage=command.operator_damage,
                resistance=0,
                damage_type="cr",
                injury_source="area",
            ),
            ht=command.operator_ht,
            rng=rng,
            system=True,
        )
    disabled = list(t.disabled_systems)
    if object_result.injury and command.hit_location != "hull":
        disabled.append(command.hit_location)
    if item_id == t.body_id and object_result.condition.disabled:
        disabled.append("hull")
    disabled = list(dict.fromkeys(disabled))
    affected = (
        t.model_copy(
            update={
                "disabled_systems": tuple(disabled),
                "status": "crashed"
                if "hull" in disabled
                else "control-required"
                if command.hit_location in ("motive", "controls") and object_result.injury
                else t.status,
                "stress_turn": state.game_time
                if item_id == t.body_id and object_result.condition.hp <= 0
                else t.stress_turn,
                "traces": (
                    *t.traces,
                    VehicleTrace(
                        command_id=command.id,
                        reason="vehicle-hit-" + command.hit_location,
                        actor_id=item_id,
                        basic_damage=command.basic_damage,
                        injury=object_result.injury,
                        dice=tuple(d for roll in object_result.checks for d in roll),
                    ),
                ),
            }
        ),
    )
    return state, affected
