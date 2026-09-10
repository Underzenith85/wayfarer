"""Basic Set physical calculations; Campaigns fourth printing B349-354/B431.

Ordinary surfaces and jumping, with explicit gravity, atmospheric pressure,
body terminal velocity and worn/innate falling protection (B431).
"""

from decimal import ROUND_HALF_UP, Decimal
from math import ceil
from typing import Literal

from wayfarer.errors import ValidationError


def climbing(surface: str, feet: int, *, combat: bool = False) -> tuple[int, int]:
    # modifier, ordinary seconds per foot, combat seconds per foot (B349).
    rows = {
        "tree": (5, Decimal(3), Decimal(1)),
        "mountain": (0, Decimal(6), Decimal(2)),
        "wall": (-3, Decimal(15), Decimal(5)),
        "building": (-3, Decimal(30), Decimal(10)),
        "rope-up": (-2, Decimal(3), Decimal(1)),
        "rope-down": (-1, Decimal(2), Decimal("0.5")),
        "rappel": (-1, Decimal(1) / 12, Decimal(1) / 12),
    }
    if surface not in rows or feet < 1:
        raise ValidationError("Unsupported climb surface or distance")
    modifier, ordinary, fast = rows[surface]
    return modifier, ceil(feet * (fast if combat else ordinary))


def jump_distance(
    basic_move: int,
    *,
    kind: Literal["high", "broad"],
    run_yards: int = 0,
    jumping: int | None = None,
    prepared: bool = True,
) -> Decimal:
    """Returns yards; source jumping uses Basic Move, not optional load reductions."""
    if basic_move < 0 or run_yards < 0 or jumping is not None and jumping < 1:
        raise ValidationError("Invalid jumping context")
    move = max(basic_move, jumping // 2 if jumping is not None else 0)
    factor, subtract, divisor = (6, 10, 36) if kind == "high" else (2, 3, 3)
    standing = max(0, factor * move - subtract)
    running = min(2 * standing, max(0, factor * (move + run_yards) - subtract))
    return Decimal(running) / divisor / (1 if prepared else 2)


def lift_limit(st: int, kind: str, *, margin: int = 0) -> tuple[Decimal, int]:
    rows = {
        "ready": (1, 1),
        "one-hand": (2, 2),
        "two-hand": (8, 4),
        "shove": (12, 1),
        "carry-back": (15, 1),
        "shift": (50, 1),
    }
    if st < 1 or kind not in rows or margin < 0:
        raise ValidationError("Unsupported lifting construction")
    multiple, seconds = rows[kind]
    lift = Decimal(st * st) / 5
    if lift >= 10:
        lift = lift.to_integral_value(rounding=ROUND_HALF_UP)
    return lift * multiple * (1 + Decimal(margin) / 20), seconds


def hiking_miles(
    move: int, *, success: bool, terrain: str = "average", bad_weather: bool = False
) -> Decimal:
    terrain_factors = {
        "very-bad": Decimal("0.2"),
        "bad": Decimal("0.5"),
        "average": Decimal(1),
        "good": Decimal("1.25"),
    }
    if move < 0 or terrain not in terrain_factors:
        raise ValidationError("Invalid hiking context")
    return (
        10
        * Decimal(move)
        * (Decimal("1.2") if success else 1)
        * terrain_factors[terrain]
        / (2 if bad_weather else 1)
    )


def swimming_yards(basic_move: int, seconds: int, encumbrance: int) -> Decimal:
    if basic_move < 0 or seconds < 0 or not 0 <= encumbrance <= 4:
        raise ValidationError("Invalid swimming context")
    return Decimal(max(1, basic_move // 5) * seconds * (5 - encumbrance)) / 5


def falling_damage(
    hp: int,
    yards: Decimal,
    *,
    hard: bool = True,
    controlled: bool = False,
    gravity: Decimal = Decimal(1),
    pressure: Decimal = Decimal(1),
    terminal_velocity: int = 60,
) -> tuple[int, int]:
    if (
        hp < 1
        or not yards.is_finite()
        or yards < 0
        or not gravity.is_finite()
        or gravity <= 0
        or not pressure.is_finite()
        or pressure < 0
        or terminal_velocity < 1
    ):
        raise ValidationError("Invalid falling context")
    distance = max(Decimal(0), yards - (5 if controlled else 0))
    velocity = (Decimal("21.4") * gravity * distance).sqrt()
    if pressure:
        velocity = min(velocity, terminal_velocity * (gravity / pressure).sqrt())
    velocity = velocity.to_integral_value(rounding=ROUND_HALF_UP)
    dice = Decimal(hp * velocity * (2 if hard else 1)) / 100
    if dice == 0:
        return 0, 0
    if dice < 1:
        return 1, -3 if dice <= Decimal("0.25") else -2 if dice <= Decimal("0.5") else -1
    return int(dice.to_integral_value(rounding=ROUND_HALF_UP)), 0


def falling_injury(basic: int, armor_dr: int, innate_dr: int = 0) -> int:
    """B431: worn armor is flexible for falls; innate DR causes no blunt trauma."""
    if min(basic, armor_dr, innate_dr) < 0:
        raise ValidationError("Invalid falling protection")
    damage = max(0, basic - innate_dr)
    penetration = max(0, damage - armor_dr)
    return penetration if penetration else damage // 5


def contagion_modifier(contacts: tuple[str, ...]) -> int:
    """B443 uses the least advantageous applicable contact, never their sum."""
    modifiers = {
        "avoided": 4,
        "dwelling": 3,
        "conversation": 2,
        "touch": 1,
        "belongings": 0,
        "cooked-flesh": 0,
        "raw-flesh": -1,
        "prolonged": -2,
        "intimate": -3,
    }
    if not contacts or any(c not in modifiers for c in contacts):
        raise ValidationError("Unknown disease contact")
    return min(modifiers[c] for c in contacts)
