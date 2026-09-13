"""Pure Campaigns B393-B394 tactical formulas."""

from wayfarer.errors import ValidationError


def personal_high_speed_velocity(basic_move: int) -> int:
    """Maximum velocity on the turn after entering personal high-speed movement."""
    if type(basic_move) is not int or basic_move < 1:
        raise ValidationError("High-speed movement requires positive Basic Move")
    return basic_move + max(1, basic_move // 5)


def high_speed_turning_radius(velocity: int, basic_move: int) -> int:
    if any(type(value) is not int or value < 1 for value in (velocity, basic_move)):
        raise ValidationError("Turning radius requires positive integer movement values")
    return velocity // basic_move
