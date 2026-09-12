"""Basic Set vehicle movement/control calculations on authored hex geometry.

B394-395 and B468-469: planar courses, authored air altitude trajectories, and
mode-specific control loss. Persisted aftermath is resolved by vehicle_resolution.
"""

from fractions import Fraction

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Modifier, Outcome, RandomSource, draw_dice, evaluate_success
from wayfarer.rules.transport_types import Transport
from wayfarer.rules.vehicle_types import VehicleTrace
from wayfarer.simulation.hex_geometry import DIRECTIONS, Hex, HexBattlefield, neighbor
from wayfarer.simulation.vehicle_commands import VehicleControl, VehicleManeuver


def ground_cruising_speed(
    top_speed: int,
    acceleration: int,
    locomotion: str,
    terrain: str,
    *,
    road_bound: bool = False,
    on_road: bool = False,
) -> Fraction:
    """B466 overland mph before convoy and weather modifiers."""
    if (
        type(top_speed) is not int
        or top_speed < 1
        or type(acceleration) is not int
        or acceleration < 1
    ):
        raise ValidationError("Ground travel requires positive vehicle ratings")
    if locomotion not in {
        "ground-wheeled",
        "ground-tracked",
        "ground-drawn",
        "ground-walking",
        "ground-slithering",
    }:
        raise ValidationError("Ground travel tables require a ground vehicle")
    if terrain not in {"very-bad", "bad", "average", "good"}:
        raise ValidationError("Unknown ground travel terrain")
    if road_bound and not on_road:
        top_speed = min(top_speed, 4 * acceleration)
    wheeled = locomotion in {"ground-wheeled", "ground-drawn"}
    multiplier = {
        "very-bad": Fraction(1, 10)
        if wheeled
        else Fraction(3, 20)
        if locomotion == "ground-tracked"
        else Fraction(1, 5),
        "bad": Fraction(1, 4) if wheeled else Fraction(1, 2),
        "average": Fraction(1, 2) if wheeled else Fraction(1),
        "good": Fraction(5, 4),
    }[terrain]
    return top_speed * multiplier


def safe_deceleration(t: Transport) -> int:
    if t.locomotion == "ground-wheeled":
        return 5
    if t.locomotion.startswith("ground-"):
        return t.acceleration if t.locomotion == "ground-mount" else 10
    if t.locomotion == "space":
        # No assumed drag/braking for spacecraft: acceleration is thrust.
        raise ValidationError("Space thrust/braking requires an explicit navigation adapter")
    return max(1, 5 + t.handling)


def footprint(t: Transport) -> frozenset[Hex]:
    if t.footprint_offsets:
        cells = []
        for q, r in t.footprint_offsets:
            for _ in range(t.facing):
                q, r = -r, q + r
            cells.append(Hex(q=t.q + q, r=t.r + r))
        return frozenset(cells)
    dq, dr = DIRECTIONS[t.facing]
    return frozenset(Hex(q=t.q + dq * o, r=t.r + dr * o) for o in t.footprint)


def move_vehicle(
    t: Transport,
    command: VehicleManeuver,
    board: HexBattlefield,
    occupied: frozenset[Hex],
    rng: RandomSource,
    ht: int,
    modifiers: tuple[Modifier, ...] = (),
) -> Transport:
    if t.status != "controlled":
        raise ValidationError("Resolve vehicle control consequences before movement")
    if t.locomotion == "ground-mount":
        raise ValidationError("Mounted maneuvers remain owned by #396")
    if board.profile_id != t.profile_id:
        raise ValidationError("Vehicle and map rules profiles differ")
    if command.end_speed > t.top_speed:
        raise ValidationError("Vehicle exceeds top speed")
    if t.locomotion != "air" and command.end_altitude is not None:
        raise ValidationError("Only air movement accepts an authored altitude")
    deceleration = safe_deceleration(t)
    entering_high = t.speed <= t.acceleration < command.end_speed
    acceleration_base = t.acceleration if entering_high else t.speed
    if (
        command.end_speed - acceleration_base > t.acceleration
        or t.speed - command.end_speed > 2 * deceleration
    ):
        raise ValidationError("Braking exceeds twice the safe limit or acceleration exceeds rating")
    risks: list[tuple[Transport, int]] = []
    high = max(t.speed, command.end_speed) > t.acceleration
    budget = t.speed if high else command.end_speed
    if high and t.speed <= t.acceleration:
        budget = t.acceleration  # B394: full Basic Move before accelerating.
    point = Hex(q=t.q, r=t.r)
    end_altitude = t.altitude if command.end_altitude is None else command.end_altitude
    if t.locomotion == "air" and abs(end_altitude - t.altitude) > budget:
        raise ValidationError("Flight trajectory exceeds the selected movement budget")
    base = board.cell(point).ground
    base_extra = board.cell(point).extra_cost
    facing = t.facing
    straight = t.straight_yards
    cost = extra = turns = 0
    radius = max(1, t.speed // t.acceleration)
    for index, direction in enumerate((facing, *command.course)):
        sweep: frozenset[Hex] = frozenset()
        if index:
            change = min((direction - facing) % 6, (facing - direction) % 6)
            if high and change and (change > 1 or straight < radius):
                if command.control_skill is None:
                    raise ValidationError(
                        "Early or tight high-speed turn requires a control maneuver"
                    )
                risks.append(
                    (
                        t.model_copy(
                            update={
                                "q": point.q,
                                "r": point.r,
                                "facing": facing,
                                "remaining_points": max(0, budget - cost),
                            }
                        ),
                        -max(0, (t.speed - t.acceleration) // t.acceleration),
                    )
                )
            if change:
                clockwise = (direction - facing) % 6 <= 3
                for rotation in range(1, change + 1):
                    heading = (facing + rotation * (1 if clockwise else -1)) % 6
                    sweep |= footprint(
                        t.model_copy(update={"q": point.q, "r": point.r, "facing": heading})
                    )
                turns += change
                straight = 0
            facing = direction
            point = neighbor(point, direction)
            straight += 1
        pose = t.model_copy(update={"q": point.q, "r": point.r, "facing": facing})
        surcharge = 0
        for cell in footprint(pose) | sweep:
            terrain = board.cell(cell)
            if cell in occupied:
                raise ValidationError("Vehicle path intersects another occupant")
            if t.locomotion.startswith("ground-"):
                if terrain.blocked:
                    raise ValidationError("Vehicle path requires collision resolution")
                if terrain.ground != base and not (terrain.extra_cost or base_extra):
                    raise ValidationError("Vehicle slope requires an authored movement surcharge")
                if cell in footprint(pose):
                    surcharge = max(surcharge, terrain.extra_cost)
            elif t.locomotion == "air":
                altitude = t.altitude + (end_altitude - t.altitude) * index // max(1, budget)
                if altitude <= terrain.ground + terrain.opaque_height:
                    raise ValidationError("Flight path intersects terrain")
            else:
                if command.waterline is None:
                    raise ValidationError("Water movement requires an authored waterline")
                if t.locomotion == "water":
                    if command.waterline - terrain.ground < Fraction(
                        t.draft * 12 + t.draft_inches, 12
                    ):
                        raise ValidationError("Insufficient water depth for vehicle draft")
                elif not terrain.ground < t.altitude < command.waterline:
                    raise ValidationError("Underwater path intersects surface or bottom")
        if index:
            extra += surcharge
            cost += 1 + surcharge
            base = board.cell(point).ground
            base_extra = board.cell(point).extra_cost
    if entering_high and (turns > 1 or extra):
        raise ValidationError("Entering high speed requires a full straight ordinary move")
    if cost != budget:
        raise ValidationError("Course must consume the complete selected movement budget")
    speed = max(0, command.end_speed - extra)
    braking = t.speed - speed
    if braking > 2 * deceleration:
        return t.model_copy(
            update={
                "q": point.q,
                "r": point.r,
                "facing": facing,
                "speed": speed,
                "straight_yards": straight,
                "remaining_points": speed,
                "status": "skidding",
                "attack_penalty": min(-1, -(braking - 2 * deceleration)),
                "aim_lost": True,
                "traces": (
                    *t.traces,
                    VehicleTrace(
                        command_id=command.id,
                        reason="automatic-terrain-control-loss",
                        actor_id=t.operator_id,
                        target=0,
                        margin=-(braking - 2 * deceleration),
                    ),
                ),
            }
        )
    if braking > deceleration:
        if command.control_skill is None:
            raise ValidationError("Terrain or emergency braking requires a control maneuver")
        risks.append(
            (
                t.model_copy(
                    update={"q": point.q, "r": point.r, "facing": facing, "remaining_points": 0}
                ),
                -((braking - deceleration) // 2),
            )
        )
    if t.locomotion == "air" and speed < t.minimum_speed:
        raise ValidationError("Flight below minimum speed requires stall resolution")
    traces = t.traces
    for before, modifier in risks:
        assert command.control_skill is not None
        checked = control_vehicle(
            before,
            VehicleControl(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=command.expected_revision,
                transport_id=t.id,
                skill=command.control_skill,
                modifier=modifier,
                turning=True,
            ),
            rng,
            ht,
            modifiers,
        )
        traces = (*traces, *checked.traces[len(before.traces) :])
        if checked.status != "controlled":
            return checked.model_copy(
                update={"traces": traces, "remaining_points": before.remaining_points}
            )
    return t.model_copy(
        update={
            "traces": traces,
            "q": point.q,
            "r": point.r,
            "facing": facing,
            "speed": speed,
            "altitude": end_altitude,
            "vertical_speed": 0,
            "straight_yards": straight,
            "attack_penalty": 0,
            "aim_lost": False,
        }
    )


def control_vehicle(
    t: Transport,
    command: VehicleControl,
    rng: RandomSource,
    ht: int,
    modifiers: tuple[Modifier, ...] = (),
) -> Transport:
    if t.locomotion == "ground-mount":
        raise ValidationError("Mount control uses Riding and the mounted loss table")
    recovering = t.locomotion == "air" and t.status in ("diving", "stalled")
    if t.status not in ("controlled", "control-required") and not recovering:
        raise ValidationError("Vehicle has unresolved control aftermath")
    target = command.skill + t.handling + command.modifier - (5 if recovering else 0)
    check = evaluate_success(
        target,
        modifiers,
        draw_dice(rng),
        rules_package=t.profile_id,
        rules_version="2",
        rule_id="vehicle:control",
    )
    changes: dict[str, object] = {
        "control_dice": check.dice,
        "control_target": check.effective_target,
        "control_margin": check.margin,
    }
    traces = [
        VehicleTrace(
            command_id=command.id,
            reason="control",
            actor_id=t.operator_id,
            dice=check.dice,
            target=check.effective_target,
            margin=check.margin,
        )
    ]
    if check.outcome.succeeded:
        if recovering or t.status == "control-required":
            changes["status"] = "controlled"
            if recovering:
                changes["vertical_speed"] = 0
    else:
        severe = check.outcome == Outcome.CRITICAL_FAILURE or -check.margin > t.stability
        changes.update(aim_lost=True, attack_penalty=min(-1, check.margin))
        if t.locomotion.startswith("ground-"):
            changes["status"] = "crashed" if severe else "skidding"
            if severe:
                changes["skid_thirds"] = t.speed
            else:
                changes["remaining_points"] = t.speed
                if not command.turning:
                    veer = rng.randbelow(2)
                    changes["facing"] = (t.facing + (-1 if veer == 0 else 1)) % 6
                    traces.append(
                        VehicleTrace(
                            command_id=command.id, reason="veer", actor_id=t.body_id, dice=(veer,)
                        )
                    )
        elif t.locomotion == "air":
            if severe or recovering:
                changes["status"] = "stalled" if command.climbing else "diving"
            else:
                changes.update(
                    altitude=t.altitude - 5,
                    speed=max(0, t.speed - 10),
                    remaining_points=max(0, t.speed - 10),
                    status="drifting",
                )
                if t.speed - 10 < t.minimum_speed:
                    changes["status"] = "stalled"
                elif t.altitude - 5 <= 0:
                    changes["status"] = "crashed"
        elif t.locomotion == "water":
            changes["status"] = (
                ("capsized" if t.unsinkable else "sinking") if severe else "drifting"
            )
        else:
            changes["status"] = "drifting"
            if not severe and t.locomotion == "underwater":
                changes["altitude"] = t.altitude + 5  # loses depth, not altitude (B469)
            if severe:
                stress = evaluate_success(
                    ht,
                    (),
                    draw_dice(rng),
                    rules_package=t.profile_id,
                    rules_version="2",
                    rule_id="vehicle:stress",
                )
                traces.append(
                    VehicleTrace(
                        command_id=command.id,
                        reason="stress",
                        actor_id=t.body_id,
                        dice=stress.dice,
                        target=ht,
                        margin=stress.margin,
                    )
                )
                if not stress.outcome.succeeded:
                    changes["status"] = "stress-failure"
    changes["traces"] = (*t.traces, *traces)
    return t.model_copy(update=changes)
