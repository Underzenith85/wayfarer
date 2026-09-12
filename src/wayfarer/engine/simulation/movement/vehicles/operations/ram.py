"""Deliberate collisions."""

from typing import Literal

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.rules.types.vehicle import VehicleTrace, WaterOccupantCheck
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, distance
from wayfarer.engine.simulation.movement.vehicles.collisions import (
    durability,
    impact,
    internal_id,
)
from wayfarer.engine.simulation.movement.vehicles.commands import (
    VehicleImpact,
    VehicleRam,
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
    assert isinstance(command, VehicleRam)
    t = operation.transport
    health = operation.health
    rng = operation.rng
    affected: tuple[Transport, ...] = (t,)
    target = next(
        (vehicle for vehicle in state.transports if vehicle.id == command.target_transport_id),
        None,
    )
    if (
        t.locomotion == "ground-mount"
        or target is None
        or target.id == t.id
        or target.mechanics_version != 2
        or target.locomotion == "ground-mount"
    ):
        raise ValidationError("Ramming requires two distinct version-two vehicles")
    durability(engine, state, target.body_id)
    if (
        t.speed == 0
        or t.altitude != target.altitude
        or distance(Hex(q=t.q, r=t.r), Hex(q=target.q, r=target.r)) > 1
    ):
        raise ValidationError("Declared ram requires an adjacent target at the same altitude")
    if (command.defense == "dodge") != (command.defender_skill is not None):
        raise ValidationError("Ram Dodge requires exactly one compiled defense value")
    target_operator = next(p for p in state.pools if p.id == "hp:" + target.operator_id)
    assert target_operator.injury is not None
    if command.defender_skill is not None and (
        target.operator_id not in target.occupants
        or target_operator.injury.incapacitated
        or target_operator.injury.stunned
    ):
        raise ValidationError("Ram Dodge requires an able target operator")
    attack = success_roll(
        t.profile_id,
        max(1, command.skill + t.handling + t.attack_penalty),
        check_modifiers(state, t.operator_id, "dx"),
        rng=rng,
    )
    ram_traces = [
        VehicleTrace(
            command_id=command.id,
            reason="declared-ram-attack",
            actor_id=t.operator_id,
            dice=attack.dice,
            target=attack.effective_target,
            margin=attack.margin,
        )
    ]
    defended = False
    if attack.outcome.succeeded and command.defender_skill is not None:
        defense = success_roll(
            target.profile_id,
            max(1, command.defender_skill + target.handling),
            check_modifiers(state, target.operator_id, "dx"),
            rng=rng,
        )
        defended = defense.outcome.succeeded
        ram_traces.append(
            VehicleTrace(
                command_id=command.id,
                reason="declared-ram-defense",
                actor_id=target.operator_id,
                dice=defense.dice,
                target=defense.effective_target,
                margin=defense.margin,
            )
        )
    t = t.model_copy(
        update={
            "attack_penalty": 0,
            "aim_lost": False,
            "traces": (*t.traces, *ram_traces),
        }
    )
    if not attack.outcome.succeeded or defended:
        affected = (t,)
    else:
        if health is None:
            raise ValidationError("Ram collision requires compiled occupant HT")
        collision = VehicleImpact(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
            transport_id=t.id,
            target_transport_id=target.id,
            angle=command.angle,
            speed_after=command.speed_after,
            target_speed_after=command.target_speed_after,
            protection=command.protection,
        )
        state, affected = impact(engine, state, collision, t, target, health, rng)
    return state, affected
