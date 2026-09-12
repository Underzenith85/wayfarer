"""Pure tactical geometry for the frozen Basic Set profile; no campaign mutation.

One axial hex is one yard. See docs/tactical-geometry.md for source provenance,
edge conventions, limitations, and the explicit integration/migration boundary.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Literal

from pydantic import Field, model_validator
from pydantic.config import JsonDict

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record
from wayfarer.rules.conformance import BASELINE_ID

HexFacing = Literal[0, 1, 2, 3, 4, 5]
HexPosture = Literal["standing", "crouching", "kneeling", "crawling", "sitting", "lying"]
Arc = Literal["front", "right", "rear", "left", "close"]
DIRECTIONS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))


def _omitted_default(schema: JsonDict) -> None:
    """These defaults are omitted on the wire, so clients must treat them as optional."""
    schema.pop("default", None)


class Hex(Record):
    q: int = Field(ge=-1000, le=1000)
    r: int = Field(ge=-1000, le=1000)

    @property
    def cube(self) -> tuple[int, int, int]:
        return self.q, self.r, -self.q - self.r


def distance(a: Hex, b: Hex) -> int:
    return max(abs(x - y) for x, y in zip(a.cube, b.cube, strict=True))


def neighbor(origin: Hex, facing: int) -> Hex:
    if type(facing) is not int or facing not in range(6):
        raise ValidationError("Facing must be one of six hex directions")
    q, r = DIRECTIONS[facing]
    return Hex(q=origin.q + q, r=origin.r + r)


class Pose(Record):
    position: Hex
    facing: HexFacing
    posture: HexPosture = "standing"

    @model_validator(mode="before")
    @classmethod
    def strict_facing(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("facing")) is not int:
            raise ValueError("Facing must be an integer")
        return value


def arc(observer: Pose, target: Hex) -> Arc:
    """Nearest hex direction; exact sector edges use the less favorable arc."""
    if observer.position == target:
        return "close"
    dq = target.q - observer.position.q
    dr = target.r - observer.position.r
    # Dot products in the axial basis, multiplied by two to keep them integral.
    dots = tuple((2 * dq + dr) * q + (dq + 2 * dr) * r for q, r in DIRECTIONS)
    arcs: tuple[Arc, ...] = ("front", "front", "right", "rear", "left", "front")
    candidates = [arcs[(i - observer.facing) % 6] for i, dot in enumerate(dots) if dot == max(dots)]
    priority: dict[Arc, int] = {"front": 0, "left": 1, "right": 1, "rear": 2, "close": 3}
    return max(candidates, key=priority.__getitem__)


class Cell(Record):
    position: Hex
    elevation: int = Field(default=0, ge=-1000, le=1000, description="Yards above map datum")
    blocked: bool = False
    extra_cost: int = Field(default=0, ge=0, le=100, description="Authored terrain MP surcharge")
    opaque_height: int = Field(default=0, ge=0, le=1000, description="Yards above ground")
    elevation_inches: int = Field(
        default=0,
        ge=0,
        lt=36,
        description="Additional inches above elevation datum",
        exclude_if=lambda v: v == 0,
        json_schema_extra=_omitted_default,
    )

    @property
    def ground(self) -> Fraction:
        return Fraction(self.elevation * 36 + self.elevation_inches, 36)


class Stairway(Record):
    """Authored traversable edge, not permission to climb or jump a cliff."""

    start: Hex
    end: Hex


class HexBattlefield(Record):
    """New tagged contract. Legacy square maps cannot validate as hex maps."""

    id: Id
    location_id: Id = "unbound"
    source_template_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    darkness_penalty: int = Field(default=0, ge=-10, le=0, exclude_if=lambda v: v == 0)
    coordinate_system: Literal["hex-axial-v1"]
    profile_id: Literal["gurps-basic-set-4e-2004"]
    baseline_id: Literal["gurps-4e-characters-3p-2008+campaigns-4p-2008"]
    cells: tuple[Cell, ...] = Field(min_length=1, max_length=10000)
    stairs: tuple[Stairway, ...] = Field(
        default=(), exclude_if=lambda v: not v, json_schema_extra=_omitted_default
    )

    @model_validator(mode="after")
    def distinct_cells(self) -> HexBattlefield:
        if len({cell.position for cell in self.cells}) != len(self.cells):
            raise ValueError("Duplicate hex cell")
        edges: set[frozenset[Hex]] = set()
        for stair in self.stairs:
            edge = frozenset((stair.start, stair.end))
            if distance(stair.start, stair.end) != 1 or edge in edges:
                raise ValueError("Stairs require unique adjacent endpoints")
            edges.add(edge)
            for point in edge:
                if self.cell(point).blocked:
                    raise ValueError("Stairs cannot connect blocked cells")
        return self

    def stairway(self, start: Hex, end: Hex) -> bool:
        return any({s.start, s.end} == {start, end} for s in self.stairs)

    def cell(self, position: Hex) -> Cell:
        for cell in self.cells:
            if cell.position == position:
                return cell
        raise ValidationError("Hex is outside the battlefield")


class Occupant(Record):
    actor_id: Id
    position: Hex


class Movement(Record):
    origin: Pose
    destination: Pose
    cost: Decimal = Field(ge=0)
    path: tuple[Hex, ...]
    baseline_id: str = BASELINE_ID


def step_allowance(move: int) -> int:
    if type(move) is not int or move < 0:
        raise ValidationError("Move must be a nonnegative integer")
    return max(1, (move + 9) // 10)


def posture_move(move: int, posture: HexPosture) -> int:
    """B367/B387: crouching 2/3 Move; crawling 1/3; kneeling 1/3."""
    step_allowance(move)  # shared validation
    if posture == "standing":
        return move
    if posture == "crouching":
        return move * 2 // 3
    if posture in ("kneeling", "crawling"):
        return move // 3
    if posture in ("sitting", "lying"):
        return 0
    raise ValidationError("Unknown posture")


def _occupancy(battlefield: HexBattlefield, occupants: tuple[Occupant, ...]) -> None:
    if len({entry.actor_id for entry in occupants}) != len(occupants):
        raise ValidationError("Duplicate occupant actor")
    for entry in occupants:
        if battlefield.cell(entry.position).blocked:
            raise ValidationError("Occupant is in a blocked hex")


def movement(
    battlefield: HexBattlefield,
    origin: Pose,
    path: tuple[Hex, ...],
    *,
    move: int,
    occupants: tuple[Occupant, ...] = (),
    actor_id: str | None = None,
    step: bool = False,
    enter_close_combat: bool = False,
    final_facing: HexFacing | None = None,
    turns: tuple[HexFacing, ...] = (),
    final_turn_policy: Literal["move", "one", "any"] = "move",
) -> Movement:
    """Validate a supplied path and return a receipt, never search or mutate.

    Paths exclude the origin. Forward movement turns to its direction for free;
    side/back movement preserves facing. Final turns follow maneuver policy and
    the spent movement budget; Step permits any facing. Occupied entry requires
    explicit close combat and may only end a path. Elevation transitions require
    authored stairs or a separate physical-feat ruling.
    """
    step_allowance(move)
    _occupancy(battlefield, occupants)
    if battlefield.cell(origin.position).blocked:
        raise ValidationError("Origin is blocked")
    if origin.posture == "sitting" and path:
        raise ValidationError("Posture requires a change before hex movement")
    if path and move == 0:
        raise ValidationError("Zero Move does not permit movement")
    if turns and len(turns) != len(path):
        raise ValidationError("Supply one pre-move facing per path hex")
    budget = step_allowance(move) if step else move
    pose = origin
    total = Decimal(0)
    for index, target in enumerate(path):
        if turns:
            facing = turns[index]
            updated = Pose(position=pose.position, facing=facing, posture=pose.posture)
            total += 0 if step else min((facing - pose.facing) % 6, (pose.facing - facing) % 6)
            pose = updated
        if distance(pose.position, target) != 1:
            raise ValidationError("Movement path must use adjacent hexes")
        cell = battlefield.cell(target)
        if cell.blocked:
            raise ValidationError("Movement path is blocked")
        stair = battlefield.stairway(pose.position, target)
        if cell.ground != battlefield.cell(pose.position).ground and not stair:
            raise ValidationError("Elevation transition requires physical-feat resolution")
        occupied = any(o.position == target and o.actor_id != actor_id for o in occupants)
        if occupied and not (enter_close_combat and index == len(path) - 1):
            raise ValidationError("Occupied hex requires explicit close-combat entry")
        direction = DIRECTIONS.index((target.q - pose.position.q, target.r - pose.position.r))
        forward = (direction - pose.facing) % 6 in (0, 1, 5)
        if step:
            total += 1
        elif origin.posture == "lying":
            total += max(1, move)
        else:
            posture_cost = (
                Decimal("0.5")
                if origin.posture == "crouching"
                else Decimal(2)
                if origin.posture in ("kneeling", "crawling")
                else Decimal(0)
            )
            total += (1 if forward else 2) + cell.extra_cost + int(stair) + posture_cost
        pose = Pose(
            position=target,
            facing=direction if forward and not step else pose.facing,  # type: ignore[arg-type]
            posture=origin.posture,
        )
    if final_facing is not None:
        updated = Pose(position=pose.position, facing=final_facing, posture=pose.posture)
        turn = min((final_facing - pose.facing) % 6, (pose.facing - final_facing) % 6)
        freely_turn = (
            final_turn_policy == "any" or final_turn_policy == "move" and total * 2 <= budget
        )
        if not step and not freely_turn and turn > 1:
            raise ValidationError("Only one free final facing change after half Move")
        pose = updated
    if total > budget and not (not step and len(path) == 1 and not turns):
        raise ValidationError("Movement exceeds allowance")
    return Movement(origin=origin, destination=pose, cost=total, path=path)


def in_reach(
    battlefield: HexBattlefield,
    attacker: Pose,
    target: Hex,
    *,
    reaches: frozenset[int],
) -> bool:
    """B388/B402-403 reach; location/defense height effects are separate."""
    if not reaches or any(type(value) is not int or value < 0 for value in reaches):
        raise ValidationError("Reach must contain nonnegative integer distances")
    start, end = battlefield.cell(attacker.position), battlefield.cell(target)
    effective_height = max(Fraction(0), abs(start.ground - end.ground) - max(0, max(reaches) - 1))
    return (
        not start.blocked
        and not end.blocked
        and effective_height <= 2
        and distance(attacker.position, target) in reaches
        and arc(attacker, target) in ("front", "close")
    )


def ranged_distance(
    battlefield: HexBattlefield, attacker: Hex, target: Hex, *, beam: bool = False
) -> Fraction:
    """Return B407 effective range for a shot between two battlefield cells.

    Hex distance is the real ground distance. Shooting uphill adds the full
    elevation difference; shooting downhill subtracts half of it, but never
    reduces effective range below half the ground distance.
    """
    start, end = battlefield.cell(attacker), battlefield.cell(target)
    ground_distance = Fraction(distance(attacker, target))
    if ground_distance == 0:
        raise ValidationError("Ranged close-combat handling remains unsupported")
    if beam:
        return ground_distance
    elevation = end.ground - start.ground
    if elevation > 0:
        return ground_distance + elevation
    if elevation < 0:
        return max(ground_distance / 2, ground_distance + elevation / 2)
    return ground_distance


class RetreatContext(Record):
    """Authoritative turn/condition inputs, owned by the combat consumer."""

    already_retreated: bool = False
    stunned: bool = False
    grappled: bool = False
    maneuver_allows_retreat: bool = True
    moved_more_than_basic_move: bool = False


def can_retreat(
    battlefield: HexBattlefield,
    defender: Pose,
    attacker: Hex,
    destination: Hex,
    *,
    context: RetreatContext,
    occupants: tuple[Occupant, ...] = (),
) -> bool:
    """One-hex retreat eligibility (B377, B391); no defense bonus or turn mutation."""
    battlefield.cell(attacker)
    battlefield.cell(defender.position)
    _occupancy(battlefield, occupants)
    if (
        context.already_retreated
        or context.stunned
        or context.grappled
        or not context.maneuver_allows_retreat
        or context.moved_more_than_basic_move
        or defender.posture in ("sitting", "lying")
        or distance(defender.position, destination) != 1
        or distance(attacker, destination) <= distance(attacker, defender.position)
        or any(entry.position == destination for entry in occupants)
    ):
        return False
    try:
        movement(battlefield, defender, (destination,), move=1, step=True)
    except ValidationError:
        return False
    return True


def _intersection(a: Hex, b: Hex, cell: Hex) -> tuple[Fraction, Fraction] | None:
    """Clip a center-to-center segment against a closed regular hex, exactly."""
    low, high = Fraction(0), Fraction(1)
    start = tuple(x - c for x, c in zip(a.cube, cell.cube, strict=True))
    delta = tuple(y - x for x, y in zip(a.cube, b.cube, strict=True))
    for i, j in ((0, 1), (1, 2), (2, 0)):
        for sign in (-1, 1):
            offset = sign * (start[i] - start[j])
            slope = sign * (delta[i] - delta[j])
            if slope == 0:
                if offset > 1:
                    return None
            elif slope > 0:
                high = min(high, Fraction(1 - offset, slope))
            else:
                low = max(low, Fraction(1 - offset, slope))
            if low > high:
                return None
    return low, high


class SightPoint(Record):
    position: Hex
    height: int = Field(ge=0, le=100, description="Height above ground in yards")


def line_of_sight(battlefield: HexBattlefield, start: SightPoint, end: SightPoint) -> bool:
    """Geometric visibility only; does not reveal entities or apply perception.

    Closed hex boundaries block on either side. Missing cells are outside the
    map and opaque. Ground and authored opaque columns occlude the exact ray;
    blocked movement alone does not imply opaque terrain.
    """
    a, b = battlefield.cell(start.position), battlefield.cell(end.position)
    z0, z1 = a.ground + start.height, b.ground + end.height
    cells = {cell.position: cell for cell in battlefield.cells}
    # Every intersected hex lies within one coordinate of a sample spaced at
    # most one hex apart. This corridor is O(distance), not map bounding-box area.
    count = max(1, distance(start.position, end.position))
    candidates: set[Hex] = set()
    for index in range(count + 1):
        q0 = round(Fraction(start.position.q * (count - index) + end.position.q * index, count))
        r0 = round(Fraction(start.position.r * (count - index) + end.position.r * index, count))
        for q in range(max(-1000, q0 - 1), min(1000, q0 + 1) + 1):
            for r in range(max(-1000, r0 - 1), min(1000, r0 + 1) + 1):
                candidates.add(Hex(q=q, r=r))
    for position in candidates:
        interval = _intersection(start.position, end.position, position)
        if interval is None:
            continue
        cell = cells.get(position)
        if cell is None:
            return False
        low, high = interval
        ray_low = min(z0 + low * (z1 - z0), z0 + high * (z1 - z0))
        # Endpoint ground itself does not obscure an observer on the ground.
        if position in (start.position, end.position) and cell.opaque_height == 0:
            continue
        if cell.ground + cell.opaque_height >= ray_low:
            return False
    return True
