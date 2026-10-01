"""Personal high-speed path validation from Campaigns B394.

Acceleration, braking, skids and vehicle operation are deliberately separate.
"""

from __future__ import annotations

from wayfarer.engine.rules.tables.tactical import (
    high_speed_turning_radius,
    personal_high_speed_velocity,
)
from wayfarer.engine.rules.types.tactical import HighSpeedState
from wayfarer.engine.simulation.hex_geometry import (
    DIRECTIONS,
    Hex,
    HexBattlefield,
    Pose,
    distance,
)
from wayfarer.errors import ValidationError


def _turn_distance(left: int, right: int) -> int:
    return min((left - right) % 6, (right - left) % 6)


def _directions(origin: Hex, path: tuple[Hex, ...]) -> tuple[int, ...]:
    points = (origin,) + path
    result: list[int] = []
    for start, end in zip(points, points[1:], strict=False):
        if distance(start, end) != 1:
            raise ValidationError("High-speed movement path must use adjacent hexes")
        result.append(DIRECTIONS.index((end.q - start.q, end.r - start.r)))
    return tuple(result)


def transition(
    battlefield: HexBattlefield,
    origin: Pose,
    path: tuple[Hex, ...],
    *,
    basic_move: int,
    maneuver: str,
    current: HighSpeedState | None,
    enter: bool,
    interrupted: bool = False,
) -> HighSpeedState | None:
    """Validate B394 direction/turn constraints and return the next saved state."""
    if current is None and not enter:
        return None
    if current is not None and enter:
        raise ValidationError("Combatant is already moving at high speed")
    if maneuver not in ("move", "move_and_attack"):
        raise ValidationError("High-speed movement requires Move or Move and Attack")
    if origin.posture != "standing":
        raise ValidationError("High-speed movement requires a standing combatant")
    if not path:
        raise ValidationError("High-speed movement requires a path")
    directions = _directions(origin.position, path)
    if any(
        battlefield.cell(point).extra_cost
        or battlefield.cell(point).ground != battlefield.cell(previous).ground
        for previous, point in zip((origin.position,) + path[:-1], path, strict=True)
    ):
        raise ValidationError("High-speed difficult terrain requires the B395 slowing rules")

    if current is None or current.entry_turns is not None:
        return _entry(origin, directions, basic_move, current, interrupted)

    required = current.remaining_yards or current.velocity
    _require_distance(len(path), required, interrupted)
    radius = high_speed_turning_radius(current.velocity, basic_move)
    direction = current.direction if current.direction is not None else origin.facing
    straight = current.straight_yards
    for next_direction in directions:
        if _turn_distance(direction, next_direction) > 1:
            raise ValidationError("High-speed movement cannot turn more than 60 degrees")
        if next_direction != direction:
            if straight < radius:
                raise ValidationError("High-speed turn occurs before the turning radius")
            direction, straight = next_direction, 0
        straight += 1
    return current.model_copy(
        update={
            "straight_yards": straight,
            "direction": direction,
            "remaining_yards": required - len(path) or None,
        }
    )


def _require_distance(length: int, required: int, interrupted: bool) -> None:
    if length > required or length != required and not interrupted:
        raise ValidationError("High-speed movement must cover the saved velocity")


def _entry(
    origin: Pose,
    directions: tuple[int, ...],
    basic_move: int,
    current: HighSpeedState | None,
    interrupted: bool,
) -> HighSpeedState:
    required = current.remaining_yards if current is not None else basic_move
    assert required is not None
    _require_distance(len(directions), required, interrupted)
    initial_direction = current.direction if current is not None else origin.facing
    assert initial_direction is not None
    if _turn_distance(initial_direction, directions[0]) > 1 or any(
        _turn_distance(left, right) > 1
        for left, right in zip(directions, directions[1:], strict=False)
    ):
        raise ValidationError("Entering high speed permits only gradual forward movement")
    turns = sum(left != right for left, right in zip(directions, directions[1:], strict=False))
    if current is not None:
        turns += (current.entry_turns or 0) + int(initial_direction != directions[0])
    if turns > 1:
        raise ValidationError("Entering high speed permits at most one change of direction")
    remaining = required - len(directions)
    return HighSpeedState(
        velocity=personal_high_speed_velocity(basic_move),
        direction=directions[-1],
        remaining_yards=remaining or None,
        entry_turns=turns if remaining else None,
    )
