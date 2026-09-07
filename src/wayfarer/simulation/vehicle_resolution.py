"""Version-two vehicle dispatch under the existing transport transaction."""

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.hazard_types import require_hazards_settled
from wayfarer.rules.recovery_types import require_settled
from wayfarer.rules.transport_types import Transport
from wayfarer.rules.vehicle_capabilities import VEHICLE_OPERATIONS
from wayfarer.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.simulation.resources import ResourceEngine, ResourceState
from wayfarer.simulation.vehicle_collisions import durability, impact
from wayfarer.simulation.vehicle_commands import (
    UpgradeVehicle,
    VehicleControl,
    VehicleImpact,
    VehicleManeuver,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.simulation.vehicle_motion import control_vehicle, footprint, move_vehicle

VehicleCommand = (
    UpgradeVehicle
    | VehicleControl
    | VehicleImpact
    | VehicleManeuver
    | VehicleRollover
    | VehicleSkid
)


def resolve_vehicle(
    engine: ResourceEngine,
    state: ResourceState,
    command: VehicleCommand,
    t: Transport,
    *,
    board: HexBattlefield | None,
    health: dict[str, int] | None,
    occupied: frozenset[Hex],
    rng: RandomSource,
) -> ResourceState:
    from wayfarer.simulation.transport import validate_transport

    if isinstance(command, UpgradeVehicle):
        if (
            t.mechanics_version != 1
            or t.locomotion != "ground-wheeled"
            or t.speed
            or t.status != "controlled"
        ):
            raise ValidationError("Upgrade requires a stopped, controlled version-one vehicle")
        t = t.model_copy(update={"mechanics_version": 2})
        return state.model_copy(
            update={"transports": tuple(t if v.id == t.id else v for v in state.transports)}
        )
    if t.mechanics_version != 2:
        raise ValidationError("Vehicle command requires explicit transport version 2 migration")
    if t.locomotion == "ground-mount":
        raise ValidationError("Version-two vehicle commands do not implement mounted combat")
    if command.kind not in VEHICLE_OPERATIONS[t.locomotion]:
        raise ValidationError("Vehicle operation requires an unsupported navigation adapter")
    durability(engine, state, t.body_id)
    occupied |= frozenset(
        cell
        for vehicle in state.transports
        if vehicle.id != t.id and vehicle.altitude == t.altitude
        for cell in footprint(vehicle)
    )
    operator = next(p for p in state.pools if p.id == "hp:" + t.operator_id)
    assert operator.injury is not None
    if isinstance(command, (VehicleManeuver, VehicleControl)) and (
        operator.injury.incapacitated or operator.injury.stunned
    ):
        raise ValidationError("Incapacitated operator cannot control a vehicle")
    affected: tuple[Transport, ...] = (t,)
    if isinstance(command, VehicleManeuver):
        if t.last_turn == state.game_time:
            raise ConflictError("Vehicle already moved this second")
        item = next(i for i in state.items if i.id == t.body_id)
        assert item.condition is not None
        if item.condition.disabled or item.condition.hp <= 0:
            raise ValidationError("Vehicle requires damage/stress resolution before movement")
        if t.subhex_thirds:
            raise ValidationError("Fractional skid endpoint requires tactical pose reconciliation")
        if board is None:
            raise ValidationError("Vehicle movement requires an authored map")
        other_cells = frozenset(
            c
            for v in state.transports
            if v.id != t.id and v.altitude == t.altitude
            for c in footprint(v)
        )
        profile = engine.specs[item.definition_id].durability
        assert profile is not None
        affected = (
            move_vehicle(t, command, board, occupied | other_cells, rng, profile.ht).model_copy(
                update={"last_turn": state.game_time}
            ),
        )
    elif isinstance(command, VehicleControl):
        item = next(i for i in state.items if i.id == t.body_id)
        profile = engine.specs[item.definition_id].durability
        assert profile is not None
        if t.open_cabin and t.locomotion == "water":
            raise ValidationError("Open-deck control requires overboard and swimming consumers")
        recovering = t.locomotion == "air" and t.status in ("diving", "stalled")
        if recovering and t.recovery_turn == state.game_time:
            raise ConflictError("Air recovery already attempted this second")
        controlled = control_vehicle(t, command, rng, profile.ht)
        if recovering:
            controlled = controlled.model_copy(update={"recovery_turn": state.game_time})
        affected = (controlled,)
    else:
        if health is None:
            raise ValidationError("Collision requires compiled occupant HT")
        if isinstance(command, VehicleSkid):
            if not t.locomotion.startswith("ground-") or t.status != "skidding":
                raise ValidationError("Vehicle has no pending ground skid")
            if board is None or board.profile_id != t.profile_id:
                raise ValidationError("Skid requires matching map")
            point = Hex(q=t.q, r=t.r)
            elevation = board.cell(point).ground
            hit = False
            for _ in range(t.remaining_points):
                destination = neighbor(point, t.facing)
                pose = t.model_copy(update={"q": destination.q, "r": destination.r})
                for cell in footprint(pose):
                    terrain = board.cell(cell)
                    if cell in occupied:
                        raise ValidationError(
                            "Skid hits another occupant; resolve its collision target"
                        )
                    if terrain.ground != elevation or terrain.extra_cost:
                        raise ValidationError("Skid requires slope or surface resolution")
                    hit |= terrain.blocked
                if hit:
                    break
                point = destination
            updated_t = t.model_copy(
                update={"q": point.q, "r": point.r, "remaining_points": 0, "status": "controlled"}
            )
            if hit:
                collision = VehicleImpact(
                    id=command.id,
                    actor_id=command.actor_id,
                    expected_revision=command.expected_revision,
                    transport_id=t.id,
                    angle="immovable",
                    protection=command.protection,
                )
                state, affected = impact(engine, state, collision, updated_t, None, health, rng)
            else:
                affected = (updated_t,)
        elif isinstance(command, VehicleRollover):
            if not t.locomotion.startswith("ground-") or t.status != "crashed" or not t.skid_thirds:
                raise ValidationError("Vehicle has no pending ground rollover")
            if board is None or board.profile_id != t.profile_id:
                raise ValidationError("Rollover requires the matching hex map")
            point = Hex(q=t.q, r=t.r)
            initial_height = board.cell(point).ground
            hit = False
            for _ in range(t.skid_thirds // 3):
                destination = neighbor(point, t.facing)
                pose = t.model_copy(update={"q": destination.q, "r": destination.r})
                for cell in footprint(pose):
                    terrain = board.cell(cell)
                    if cell in occupied:
                        raise ValidationError(
                            "Rollover hits an actor; resolve the collision target"
                        )
                    if terrain.ground != initial_height or terrain.extra_cost:
                        raise ValidationError("Rollover requires slope or surface resolution")
                    hit |= terrain.blocked
                if hit:
                    break
                point = destination
            updated_t = t.model_copy(
                update={
                    "q": point.q,
                    "r": point.r,
                    "subhex_thirds": 0 if hit else t.skid_thirds % 3,
                    "skid_thirds": 0,
                }
            )
            collision = VehicleImpact(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                transport_id=t.id,
                angle="immovable",
                protection=command.protection,
            )
            state, affected = impact(engine, state, collision, updated_t, None, health, rng)
            affected = tuple(
                v.model_copy(update={"status": "crashed"}) if v.status != "ejection-pending" else v
                for v in affected
            )
        else:
            target = next(
                (v for v in state.transports if v.id == command.target_transport_id), None
            )
            if command.target_transport_id is not None and target is None:
                raise ValidationError("Unknown collision transport target")
            if target is not None:
                validate_transport(engine, state, target)
                actors = frozenset((*target.occupants, target.body_id))
                require_settled(state.recovery_tasks, actors, state.game_time)
                require_hazards_settled(state.hazards, actors, state.game_time)
            state, affected = impact(engine, state, command, t, target, health, rng)
    changes = {v.id: v for v in affected}
    return state.model_copy(
        update={"transports": tuple(changes.get(v.id, v) for v in state.transports)}
    )
