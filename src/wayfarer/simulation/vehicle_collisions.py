"""B430-432 collision exchange and occupant injury on authoritative object/HP reducers."""

import hashlib

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.transport_types import Transport
from wayfarer.rules.vehicle_types import PassengerProtection, VehicleTrace
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.objects import DamageObject, apply_object
from wayfarer.simulation.resources import ResourceEngine, ResourceState
from wayfarer.simulation.vehicle_commands import VehicleImpact


def collision_exchange(
    hp: int, speed: int, target_hp: int, target_speed: int, angle: str
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Returned dice are damage inflicted by each body, not damage received.

    B432: slower/struck body cannot inflict more dice than faster/striking body.
    """
    from wayfarer.simulation.transport import collision_dice

    if min(hp, target_hp) <= 0 or min(speed, target_speed) < 0:
        raise ValidationError("Invalid collision body facts")
    if angle == "head-on":
        velocity = speed + target_speed
    elif angle == "rear-end":
        if speed <= target_speed:
            raise ValidationError("Rear-end striker must be overtaking its target")
        velocity = speed - target_speed
    elif angle == "side-on":
        velocity = speed
    else:
        raise ValidationError("Unsupported collision angle")
    if velocity == 0:
        raise ValidationError("Collision requires nonzero relative velocity")
    first, second = hp, target_hp
    if angle == "head-on":
        if target_speed > speed:
            first = min(first, second)
        elif speed > target_speed:
            second = min(first, second)
    else:
        second = min(first, second)
    return collision_dice(first, velocity), collision_dice(second, velocity)


def roll_damage(dice: tuple[int, int], rng: RandomSource) -> tuple[int, tuple[int, ...]]:
    count, adds = dice
    rolled = tuple(rng.randbelow(6) + 1 for _ in range(count))
    return max(0, sum(rolled) + adds), rolled


def internal_id(command_id: str, suffix: str) -> str:
    return "vehicle:" + hashlib.sha256((command_id + ":" + suffix).encode()).hexdigest()


def durability(engine: ResourceEngine, state: ResourceState, body_id: str) -> tuple[int, int]:
    item = next((i for i in state.items if i.id == body_id), None)
    spec = engine.specs.get(item.definition_id) if item else None
    if item is None or item.condition is None or spec is None or spec.durability is None:
        raise ValidationError("Collision body requires initialized authoritative object durability")
    if item.condition.destroyed:
        raise ValidationError("Destroyed collision bodies require debris resolution")
    return spec.durability.hp, spec.durability.dr


def hurt_body(
    engine: ResourceEngine,
    state: ResourceState,
    operator: str,
    body: str,
    command_id: str,
    damage: int,
    rng: RandomSource,
) -> tuple[ResourceState, int]:
    state, result = apply_object(
        engine,
        state,
        DamageObject(
            id=internal_id(command_id, "body:" + body),
            actor_id=operator,
            expected_revision=state.revision,
            item_id=body,
            basic_damage=damage,
            damage_type="cr",
        ),
        system=True,
        rng=rng,
    )
    return state, result.injury


def passenger_injury(raw: int, protection: PassengerProtection) -> int:
    """B431: armor counts as flexible for falls; restraints do not become worn armor."""
    restraint = 10 if protection.airbag else 5 if protection.belted else 0
    remaining = max(0, raw - restraint - protection.innate_dr)
    penetrating = max(0, remaining - protection.worn_dr)
    return penetrating if penetrating else remaining // 5 if protection.worn_dr else 0


def impact(
    engine: ResourceEngine,
    state: ResourceState,
    command: VehicleImpact,
    t: Transport,
    target: Transport | None,
    health: dict[str, int],
    rng: RandomSource,
) -> tuple[ResourceState, tuple[Transport, ...]]:
    from wayfarer.simulation.transport import collision_dice

    vehicles = (t,) if target is None else (t, target)
    if any(v.locomotion == "ground-mount" for v in vehicles):
        raise ValidationError("Mounted collision requires #120 rider separation")
    if any(v.mechanics_version != 2 for v in vehicles):
        raise ValidationError(
            "Collision exchange requires explicit transport version 2 on both bodies"
        )
    if target is not None and target.id == t.id:
        raise ValidationError("Vehicle cannot collide with itself")
    if command.angle == "immovable":
        if target is not None or command.target_transport_id is not None:
            raise ValidationError("Immovable collision cannot have a moving transport target")
        if t.speed == 0:
            raise ValidationError("Stationary vehicle cannot hit an immovable obstacle")
        if command.speed_after and command.obstacle_item_id is None:
            raise ValidationError("Unbreakable immovable obstacle requires a complete stop")
    elif command.surface != "hard":
        raise ValidationError("Surface hardness applies only to immovable collisions")
    elif target is None or command.obstacle_item_id is not None:
        raise ValidationError("Moving collision requires exactly one transport target")
    if command.speed_after > t.speed or (target and command.target_speed_after > target.speed):
        raise ValidationError("Collision cannot increase speed through whiplash input")
    if target is None and command.target_speed_after:
        raise ValidationError("No target for target final speed")
    manifest = tuple(a for v in vehicles for a in v.occupants)
    protections = {p.actor_id: p for p in command.protection}
    if len(protections) != len(command.protection) or not set(protections) <= set(manifest):
        raise ValidationError("Duplicate or unknown passenger protection")
    for actor in manifest:
        if actor not in health or type(health[actor]) is not int or health[actor] < 1:
            raise ValidationError("Collision requires valid compiled occupant HT")
        if (
            any(i.equipped and i.owner_id == actor for i in state.items)
            and actor not in protections
        ):
            raise ValidationError("Equipped passenger requires explicit compiled armor protection")
    for vehicle in vehicles:
        for actor in vehicle.occupants:
            protection = protections.get(actor)
            belted = protection.belted if protection else vehicle.restraints == "seatbelts"
            if (
                vehicle.open_cabin
                and not belted
                and (protection is None or protection.strength is None)
            ):
                raise ValidationError("Open-cabin ejection requires compiled passenger ST")
    hp, _ = durability(engine, state, t.body_id)
    obstacle = command.obstacle_item_id
    if obstacle is not None and obstacle in {v.body_id for v in state.transports}:
        raise ValidationError("A vehicle body must be targeted as a transport")
    cap = None
    if obstacle is not None:
        obstacle_hp, obstacle_dr = durability(engine, state, obstacle)
        cap = obstacle_hp + obstacle_dr
    received: tuple[tuple[Transport, int, tuple[int, ...], int], ...]
    if target is None:
        first, first_roll = roll_damage(
            collision_dice(hp, t.speed, hard=command.surface == "hard"), rng
        )
        if cap is not None:
            first = min(first, cap)
        received = ((t, first, first_roll, command.speed_after),)
        if obstacle is not None:
            state, _ = hurt_body(engine, state, t.operator_id, obstacle, command.id, first, rng)
    else:
        target_hp, _ = durability(engine, state, target.body_id)
        first_dice, second_dice = collision_exchange(
            hp, t.speed, target_hp, target.speed, command.angle
        )
        first, first_roll = roll_damage(first_dice, rng)
        second, second_roll = roll_damage(second_dice, rng)
        received = (
            (t, second, second_roll, command.speed_after),
            (target, first, first_roll, command.target_speed_after),
        )
    updated_vehicles = []
    for vehicle, raw, rolls, after in received:
        state, injury = hurt_body(
            engine, state, t.operator_id, vehicle.body_id, command.id, raw, rng
        )
        traces = [
            VehicleTrace(
                command_id=command.id,
                reason="collision-body",
                actor_id=vehicle.body_id,
                dice=rolls,
                basic_damage=raw,
                injury=injury,
            )
        ]
        ejected = False
        for actor in vehicle.occupants:
            protection = protections.get(
                actor,
                PassengerProtection(
                    actor_id=actor,
                    belted=vehicle.restraints == "seatbelts",
                    airbag=vehicle.restraints == "airbags",
                ),
            )
            pool = next(p for p in state.pools if p.id == "hp:" + actor)
            occupant_damage, occupant_rolls = roll_damage(
                collision_dice(pool.maximum, vehicle.speed - after, hard=True), rng
            )
            injury = passenger_injury(occupant_damage, protection)
            # Preserve raw collision/armor facts separately; the existing injury
            # service applies thresholds, shock, knockdown, stun and death once.
            state, result = apply_injury(
                state,
                Wound(
                    id=internal_id(command.id, "passenger:" + actor),
                    actor_id=actor,
                    expected_revision=state.revision,
                    basic_damage=injury,
                    resistance=0,
                    damage_type="cr",
                    injury_source="area",
                ),
                ht=health[actor],
                rng=rng,
                system=True,
            )
            distance = 0
            if vehicle.open_cabin and not protection.belted:
                assert protection.strength is not None
                distance = occupant_damage // (protection.strength - 2)
            ejected |= distance > 0
            traces.append(
                VehicleTrace(
                    command_id=command.id,
                    reason="collision-passenger",
                    actor_id=actor,
                    dice=occupant_rolls,
                    basic_damage=occupant_damage,
                    injury=result.injury,
                    ejection_yards=distance,
                )
            )
        item = next(i for i in state.items if i.id == vehicle.body_id)
        assert item.condition is not None
        status = "crashed" if item.condition.disabled else "control-required"
        # Any collision is hazardous. Require a control decision even if DR
        # stopped the body damage; actual ejection placement is a live consumer.
        if ejected:
            status = "ejection-pending"
        updated_vehicles.append(
            vehicle.model_copy(
                update={
                    "speed": after,
                    "status": status,
                    "aim_lost": True,
                    "traces": (*vehicle.traces, *traces),
                }
            )
        )
    return state, tuple(updated_vehicles)
