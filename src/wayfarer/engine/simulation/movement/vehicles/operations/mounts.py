"""A rider parted from their mount."""

from decimal import Decimal
from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.physical import falling_damage
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    collision_dice,
    internal_id,
    roll_damage,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    ResolveMountSeparation,
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
    state = operation.state
    command = operation.command
    assert isinstance(command, ResolveMountSeparation)
    t = operation.transport
    health = operation.health
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    mounted_collision = bool(command.collision_speed)
    if t.locomotion != "ground-mount" or (
        not mounted_collision and t.status not in ("rider-separated", "mount-fallen")
    ):
        raise ValidationError("Mount has no pending rider separation")
    if mounted_collision and t.status != "controlled":
        raise ValidationError("Mounted collision requires a controlled, attached pair")
    if health is None or any(a not in health for a in (t.operator_id, t.body_id)):
        raise ValidationError("Mount separation requires compiled rider and mount HT")
    rider_yards = t.rider_fall_yards
    riding_trace: VehicleTrace | None = None
    if t.status == "mount-fallen":
        if command.riding_skill is None:
            raise ValidationError("A falling mount requires the rider's compiled Riding skill")
        riding = success_roll(
            t.profile_id,
            max(1, command.riding_skill - 2 + t.mount_riding_penalty),
            check_modifiers(state, t.operator_id, "dx"),
            rng=rng,
        )
        rider_yards = 2 if riding.outcome.succeeded else 3
        riding_trace = VehicleTrace(
            command_id=command.id,
            reason="mount-fall-riding",
            actor_id=t.operator_id,
            dice=riding.dice,
            target=riding.effective_target,
            margin=riding.margin,
        )
    elif command.riding_skill is not None:
        raise ValidationError("Riding follow-up only applies when the mount falls")

    traces: list[VehicleTrace] = []
    for actor, yards in (
        (t.operator_id, rider_yards),
        (t.body_id, t.mount_fall_yards),
    ):
        if not yards and not mounted_collision:
            continue
        pool = next(p for p in state.pools if p.id == "hp:" + actor)
        damage, dice = roll_damage(
            collision_dice(pool.maximum, command.collision_speed, hard=True)
            if mounted_collision
            else falling_damage(pool.maximum, Decimal(yards)),
            rng,
        )
        state, result = apply_injury(
            state,
            Wound(
                id=internal_id(command.id, "fall:" + actor),
                actor_id=actor,
                expected_revision=state.revision,
                basic_damage=damage,
                resistance=0,
                damage_type="cr",
                injury_source="area",
            ),
            ht=health[actor],
            rng=rng,
            system=True,
        )
        traces.append(
            VehicleTrace(
                command_id=command.id,
                reason="mount-separation-collision"
                if mounted_collision
                else "mount-separation-fall",
                actor_id=actor,
                dice=dice,
                basic_damage=damage,
                injury=result.injury,
            )
        )
    affected = (
        t.model_copy(
            update={
                "occupants": (),
                "status": "crashed",
                "speed": 0,
                "rider_fall_yards": 0,
                "mount_fall_yards": 0,
                "traces": (
                    *t.traces,
                    *((riding_trace,) if riding_trace is not None else ()),
                    *traces,
                ),
            }
        ),
    )
    return state, affected
