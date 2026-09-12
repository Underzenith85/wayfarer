"""Ground transport adapters on the resource ledger and existing injury/object reducers.

Campaigns Fourth Edition, fourth printing: B394-397, B430-432, B466-469.
Trusted internal commands only. Live-play integration and remaining modes are
explicit coverage blockers; this is not a player-authored damage interface.
"""

import hashlib
from typing import Annotated, Literal

from pydantic import Field

from wayfarer.engine.rules.checks import (
    NO_RANDOM,
    Outcome,
    RandomSource,
    draw_dice,
    evaluate_success,
)
from wayfarer.engine.rules.hazard_types import require_hazards_settled
from wayfarer.engine.rules.recovery_types import require_settled
from wayfarer.engine.rules.transport_types import Transport
from wayfarer.engine.simulation.condition_checks import check_modifiers
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS, Hex, HexBattlefield, neighbor
from wayfarer.engine.simulation.injury import Wound, apply_injury, impaired_movement
from wayfarer.engine.simulation.objects import DamageObject, apply_object
from wayfarer.engine.simulation.resources import (
    Command,
    Receipt,
    ResourceEngine,
    ResourceEvent,
    ResourceState,
)
from wayfarer.engine.simulation.vehicle_commands import (
    DamageVehicle,
    NavigateSpace,
    ResolveAirAftermath,
    ResolveMountSeparation,
    ResolveVehicleEjection,
    ResolveWaterAftermath,
    UpgradeVehicle,
    VehicleControl,
    VehicleImpact,
    VehicleManeuver,
    VehicleRam,
    VehicleRollover,
    VehicleSkid,
)
from wayfarer.engine.simulation.vehicle_resolution import resolve_vehicle
from wayfarer.errors import ConflictError, ValidationError


class Drive(Command):
    kind: Literal["transport-move"] = "transport-move"
    transport_id: str
    speed: int = Field(ge=0, le=100)


class ControlTransport(Command):
    kind: Literal["transport-control"] = "transport-control"
    transport_id: str
    skill: int = Field(ge=1, le=50)
    modifier: int = Field(default=0, ge=-30, le=10)
    # Mount checks are Ready attempts to calm an already spooked mount (B397).


class SpookMount(Command):
    kind: Literal["transport-spook"] = "transport-spook"
    transport_id: str


class CollideTransport(Command):
    kind: Literal["transport-collision"] = "transport-collision"
    transport_id: str
    obstacle: Literal["hard-immovable"] = "hard-immovable"


TransportCommand = Annotated[
    Drive
    | ControlTransport
    | SpookMount
    | CollideTransport
    | VehicleControl
    | VehicleImpact
    | VehicleManeuver
    | VehicleRollover
    | VehicleSkid
    | ResolveAirAftermath
    | ResolveWaterAftermath
    | NavigateSpace
    | ResolveMountSeparation
    | VehicleRam
    | DamageVehicle
    | ResolveVehicleEjection
    | UpgradeVehicle,
    Field(discriminator="kind"),
]


def collision_dice(hp: int, speed: int, *, hard: bool = False) -> tuple[int, int]:
    """B430-431, exact rounding including the sub-die bands and hard obstacles."""
    if hp <= 0 or speed < 0:
        raise ValidationError("Collision requires positive HP and nonnegative speed")
    units = hp * speed * (2 if hard else 1)
    if units == 0:
        return 0, 0
    if units < 100:
        return 1, -3 if units <= 25 else -2 if units <= 50 else -1
    return (units + 50) // 100, 0


def _damage(hp: int, speed: int, rng: RandomSource) -> int:
    count, adds = collision_dice(hp, speed, hard=True)
    return max(0, sum(rng.randbelow(6) + 1 for _ in range(count)) + adds)


def _id(command: Command, suffix: str) -> str:
    return "transport:" + hashlib.sha256((command.id + ":" + suffix).encode()).hexdigest()


def validate_transport(engine: ResourceEngine, state: ResourceState, t: Transport) -> None:
    """Explicit scenario activation validator. No inferred migration from catalog listings."""
    if t.locomotion == "ground-mount" and t.body_id not in engine.actors:
        raise ValidationError("Mount must be a world actor")
    passengers = t.occupants + t.overboard + tuple(e.actor_id for e in t.pending_ejections)
    if not set(passengers) <= engine.actors:
        raise ValidationError("Unknown transport occupant")
    for actor in passengers + ((t.body_id,) if t.locomotion == "ground-mount" else ()):
        pool = next((p for p in state.pools if p.id == "hp:" + actor), None)
        if pool is None or pool.injury is None or pool.injury.profile_id != t.profile_id:
            raise ValidationError("Transport actors require matching explicit injury profiles")
    if t.locomotion != "ground-mount":
        item = next((i for i in state.items if i.id == t.body_id), None)
        spec = engine.specs.get(item.definition_id) if item else None
        if item is None or item.condition is None or spec is None or spec.durability is None:
            raise ValidationError("Vehicle requires an initialized authoritative durability item")
        if spec.durability.profile_id != t.profile_id or item.owner_id != t.operator_id:
            raise ValidationError("Vehicle profile or operator custody mismatch")


def apply_transport(
    engine: ResourceEngine,
    state: ResourceState,
    command: TransportCommand,
    *,
    system: bool = False,
    rng: RandomSource = NO_RANDOM,
    board: HexBattlefield | None = None,
    health: dict[str, int] | None = None,
    occupied: frozenset[Hex] = frozenset(),
) -> ResourceState:
    """Persist the entire result under commit_turn CAS; retries never consume dice.

    Board/occupancy and compiled HT are supplied by trusted scenario code. Movement
    supports straight, level, unobstructed ground only; hazards/turning reject.
    """
    if not system or command.actor_id not in engine.actors:
        raise ValidationError("Transport commands require authenticated engine authority")
    engine.validate(state)
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    old = next((r for r in state.receipts if r.command_id == command.id), None)
    if old is not None:
        if old.digest != digest:
            raise ConflictError("Transport command ID reused with different payload")
        return state
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    t = next((t for t in state.transports if t.id == command.transport_id), None)
    if t is None or t.operator_id != command.actor_id:
        raise ValidationError("Unknown transport or unauthorized operator")
    validate_transport(engine, state, t)
    actors = frozenset((*t.occupants, t.body_id))
    require_settled(state.recovery_tasks, actors, state.game_time)
    require_hazards_settled(state.hazards, actors, state.game_time)
    if isinstance(
        command,
        (
            UpgradeVehicle,
            ResolveAirAftermath,
            ResolveWaterAftermath,
            NavigateSpace,
            ResolveMountSeparation,
            VehicleRam,
            DamageVehicle,
            VehicleControl,
            VehicleImpact,
            VehicleManeuver,
            VehicleRollover,
            VehicleSkid,
            ResolveVehicleEjection,
        ),
    ):
        updated = resolve_vehicle(
            engine, state, command, t, board=board, health=health, occupied=occupied, rng=rng
        )
        t = next(v for v in updated.transports if v.id == t.id)
        return _finish_transport(state, updated, command, t, digest)
    if t.mechanics_version != 1:
        raise ValidationError("Version-two transports require version-two commands")
    updated = state
    if isinstance(command, (Drive, ControlTransport)):
        if t.last_turn == state.game_time:
            raise ConflictError("Transport already acted this second")
        for actor in (t.operator_id,) + ((t.body_id,) if t.locomotion == "ground-mount" else ()):
            pool = next(p for p in state.pools if p.id == "hp:" + actor)
            assert pool.injury is not None
            if pool.injury.incapacitated or pool.injury.stunned:
                raise ValidationError("Incapacitated operator or mount cannot act")
        t = t.model_copy(update={"last_turn": state.game_time})
    if isinstance(command, Drive):
        if t.status != "controlled":
            raise ValidationError("Resolve transport control loss before movement")
        if t.locomotion == "ground-wheeled":
            item = next(i for i in state.items if i.id == t.body_id)
            assert item.condition is not None
            if item.condition.disabled or item.condition.hp <= 0:
                raise ValidationError("Damaged vehicle requires stress resolution before operation")
        limit = 5 if t.locomotion == "ground-wheeled" else t.acceleration
        if command.speed > t.top_speed or not -limit <= command.speed - t.speed <= t.acceleration:
            raise ValidationError("Unsupported acceleration or unsafe braking")
        if board is None or board.profile_id != t.profile_id:
            raise ValidationError("Ground movement requires the matching explicit hex map")
        point = Hex(q=t.q, r=t.r)
        elevation = board.cell(point).ground
        dq, dr = DIRECTIONS[t.facing]
        # High-speed acceleration/deceleration occurs at turn end (B395, B468).
        # The mount slice permits ordinary movement up to Basic Move (B396).
        if t.locomotion == "ground-mount":
            mount_hp = next(p for p in state.pools if p.id == "hp:" + t.body_id)
            if command.speed > impaired_movement(mount_hp, t.acceleration):
                raise ValidationError("Mount speed exceeds its current ordinary movement allowance")
        travel = command.speed if max(t.speed, command.speed) <= t.acceleration else t.speed
        for step in range(travel + 1):
            for offset in t.footprint:
                cell = Hex(q=point.q + dq * offset, r=point.r + dr * offset)
                terrain = board.cell(cell)
                if (
                    terrain.blocked
                    or terrain.extra_cost
                    or terrain.ground != elevation
                    or cell in occupied
                ):
                    raise ValidationError(
                        "Transport path requires unsupported terrain or collision"
                    )
            if step < travel:
                point = neighbor(point, t.facing)
        t = t.model_copy(
            update={
                "speed": command.speed,
                "q": point.q,
                "r": point.r,
                "attack_penalty": 0,
                "aim_lost": False,
            }
        )
    elif isinstance(command, SpookMount):
        if t.locomotion != "ground-mount" or t.status != "controlled":
            raise ValidationError("Only a controlled mount can become spooked")
        t = t.model_copy(update={"status": "spooked", "successes": 0, "failures": 0})
    elif isinstance(command, ControlTransport):
        mount = t.locomotion == "ground-mount"
        if (mount and t.status != "spooked") or (not mount and t.status != "controlled"):
            raise ValidationError("Unsupported control recovery; resolve the pending loss first")
        check = evaluate_success(
            command.skill + command.modifier + (0 if mount else t.handling),
            check_modifiers(state, t.operator_id, "dx"),
            draw_dice(rng),
            rules_package=t.profile_id,
            rules_version="1",
            rule_id="transport:control",
        )
        t = t.model_copy(
            update={
                "control_dice": check.dice,
                "control_target": check.effective_target,
                "control_margin": check.margin,
            }
        )
        if mount:
            successes = t.successes + 1 if check.outcome.succeeded else 0
            failures = 0 if check.outcome.succeeded else t.failures + 1
            status = t.status
            if check.outcome == Outcome.CRITICAL_SUCCESS or successes == 3:
                status = "controlled"
            elif check.outcome == Outcome.CRITICAL_FAILURE or failures == 3:
                status = "lost"
            t = t.model_copy(
                update={"status": status, "successes": successes, "failures": failures}
            )
        elif not check.outcome.succeeded:
            crash = check.outcome == Outcome.CRITICAL_FAILURE or -check.margin > t.stability
            t = t.model_copy(
                update={
                    "status": "crashed" if crash else "skidding",
                    "attack_penalty": min(-1, check.margin),
                    "aim_lost": True,
                }
            )
    else:
        if t.locomotion == "ground-mount":
            raise ValidationError("Mounted collision requires rider separation and fall resolution")
        if any(i.equipped and i.owner_id in t.occupants for i in state.items):
            raise ValidationError("Equipped occupants require armor and blunt-trauma integration")
        if t.speed == 0:
            raise ValidationError("A stationary transport cannot collide")
        if health is None or any(a not in health for a in actors if a in engine.actors):
            raise ValidationError("Collision requires compiled occupant and mount HT")
        if t.locomotion == "ground-wheeled":
            item = next(i for i in state.items if i.id == t.body_id)
            profile = engine.specs[item.definition_id].durability
            assert profile is not None
            updated, _ = apply_object(
                engine,
                updated,
                DamageObject(
                    id=_id(command, "body"),
                    actor_id=t.operator_id,
                    expected_revision=updated.revision,
                    item_id=t.body_id,
                    basic_damage=_damage(profile.hp, t.speed, rng),
                    damage_type="cr",
                ),
                system=True,
                rng=rng,
            )
        victims = t.occupants
        for actor in victims:
            pool = next(p for p in updated.pools if p.id == "hp:" + actor)
            damage = _damage(pool.maximum, t.speed, rng)
            # This slice requires unarmored occupants. Restraint DR is separate
            # from worn armor/blunt trauma, which remains an explicit blocker.
            resistance = {"none": 0, "seatbelts": 5, "airbags": 10}[t.restraints]
            updated, _ = apply_injury(
                updated,
                Wound(
                    id=_id(command, actor),
                    actor_id=actor,
                    expected_revision=updated.revision,
                    basic_damage=damage,
                    resistance=resistance,
                    damage_type="cr",
                    injury_source="area",
                ),
                ht=health[actor],
                rng=rng,
                system=True,
            )
        t = t.model_copy(
            update={
                "speed": 0,
                "status": "crashed",
                "aim_lost": True,
            }
        )
    return _finish_transport(state, updated, command, t, digest)


def _finish_transport(
    state: ResourceState,
    updated: ResourceState,
    command: TransportCommand,
    t: Transport,
    digest: str,
) -> ResourceState:
    # Nested reducers share this atomic transaction; expose one revision to CAS.
    result = updated.model_copy(
        update={
            "revision": state.revision + 1,
            "transports": tuple(
                t if existing.id == t.id else existing for existing in updated.transports
            ),
            "events": (
                *updated.events,
                ResourceEvent(
                    id=_id(command, "event"), at=state.game_time, kind=command.kind, target_id=t.id
                ),
            ),
            "receipts": (
                *updated.receipts,
                Receipt(command_id=command.id, digest=digest),
            ),
        }
    )
    return ResourceState.model_validate(result)
