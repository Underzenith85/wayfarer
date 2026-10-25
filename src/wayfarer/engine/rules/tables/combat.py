"""Stateless Basic Set combat tables and formulas."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.errors import ValidationError

Defense = Literal["dodge", "parry", "block"]
Maneuver = Literal[
    "do_nothing",
    "move",
    "change_posture",
    "aim",
    "evaluate",
    "attack",
    "feint",
    "all_out_attack",
    "move_and_attack",
    "all_out_defense",
    "concentrate",
    "ready",
    "wait",
]
Posture = Literal["standing", "crouching", "kneeling", "crawling", "sitting", "prone"]
MovementPermission = Literal["none", "step", "half", "full", "triggered"]


@dataclass(frozen=True, slots=True)
class ManeuverPermission:
    """The action-economy contract selected by one B363-366 maneuver."""

    movement: MovementPermission
    attacks: int
    defenses: tuple[Defense, ...]
    concentration: bool = False
    full_turn: bool = False


_ANY_DEFENSE: tuple[Defense, ...] = ("dodge", "parry", "block")
MANEUVER_PERMISSIONS: dict[Maneuver, ManeuverPermission] = {
    "do_nothing": ManeuverPermission("none", 0, _ANY_DEFENSE),
    "move": ManeuverPermission("full", 0, _ANY_DEFENSE),
    "change_posture": ManeuverPermission("none", 0, _ANY_DEFENSE),
    "aim": ManeuverPermission("step", 0, _ANY_DEFENSE, full_turn=True),
    "evaluate": ManeuverPermission("step", 0, _ANY_DEFENSE),
    "attack": ManeuverPermission("step", 1, _ANY_DEFENSE),
    "feint": ManeuverPermission("step", 0, _ANY_DEFENSE),
    "all_out_attack": ManeuverPermission("half", 2, ()),
    "move_and_attack": ManeuverPermission("full", 1, ("dodge", "block")),
    "all_out_defense": ManeuverPermission("step", 0, _ANY_DEFENSE),
    "concentrate": ManeuverPermission("step", 0, _ANY_DEFENSE, concentration=True, full_turn=True),
    "ready": ManeuverPermission("step", 0, _ANY_DEFENSE),
    "wait": ManeuverPermission("triggered", 0, _ANY_DEFENSE),
}


def maneuver_permission(maneuver: Maneuver) -> ManeuverPermission:
    """Return the complete static permission row for ``maneuver``."""
    return MANEUVER_PERMISSIONS[maneuver]


def full_turn_maneuver(maneuver: Maneuver, *, suppression_fire: bool = False) -> bool:
    """Full-turn status, including All-Out Attack's suppression-fire option."""
    return maneuver_permission(maneuver).full_turn or (
        maneuver == "all_out_attack" and suppression_fire
    )


def step_allowance(move: int) -> int:
    """B363/B368: one tenth of Move, rounded up, with a one-yard minimum."""
    if type(move) is not int or move < 0:
        raise ValidationError("Move must be a nonnegative integer")
    return max(1, (move + 9) // 10)


def retreat_posture_allowed(
    posture: Posture, *, legacy: Literal["basic", "hex"] | None = None
) -> bool:
    """B377: sitting/kneeling cannot retreat; lying can roll, keeping its penalty.

    Historical hosts pass their captured old representation explicitly. New
    callers use the selected source policy rather than those old omissions.
    """
    excluded = {
        None: ("sitting", "kneeling"),
        "basic": ("kneeling",),
        "hex": ("sitting", "prone"),
    }
    return posture not in excluded[legacy]


def posture_defense_modifier(posture: Posture, *, legacy: bool = False) -> int:
    """B549/B551: one posture penalty to every active defense, never an attack."""
    penalties = {"prone": -3, "kneeling": -2}
    if not legacy:
        penalties.update({"crawling": -3, "sitting": -2})
    return penalties.get(posture, 0)


def posture_melee_modifier(posture: Posture, *, legacy: bool = False) -> int:
    """B551: melee attacks only; B370 explicitly also applies this to takedown.

    This is not an attribute or skill modifier: ranged attacks and contests
    such as Feint do not acquire this penalty.
    """
    penalties = {"prone": -4, "kneeling": -2}
    if not legacy:
        penalties.update({"crouching": -2, "crawling": -4, "sitting": -2})
    return penalties.get(posture, 0)


def posture_melee_reach_allowed(posture: Posture, *, close: bool, legacy: bool = False) -> bool:
    """B551: a crawling attacker may make only Reach C melee attacks."""
    return legacy or posture != "crawling" or close


def posture_ranged_target_modifier(posture: Posture, location: HitLocation | None) -> int:
    """B551 Target column, with unresolved exposure lanes explicitly refused."""
    if posture == "standing":
        return 0
    if location == "random":
        raise ValidationError("Random hit locations against lowered targets require source review")
    if location == "vitals":
        raise ValidationError("Vitals against lowered targets require source posture mapping")
    chosen = location or "torso"
    if posture in ("crouching", "kneeling", "sitting"):
        return -2 if chosen in ("torso", "groin", "left-leg", "right-leg") else 0
    if chosen in ("left-arm", "right-arm", "left-hand", "right-hand", "skull"):
        return 0
    raise ValidationError(
        "Crawling or lying target exposure requires supported height, elevation and head facts"
    )


def posture_move_allowance(move: int, posture: Posture) -> int:
    """B367: apply posture to already-encumbered Move."""
    step_allowance(move)
    if posture == "standing":
        return move
    if posture == "crouching":
        return move * 2 // 3
    if posture in ("kneeling", "crawling"):
        return move // 3
    if posture == "prone":
        return min(1, move)
    if posture == "sitting":
        return 0
    raise ValidationError("Unknown posture")


def maneuver_move_allowance(
    maneuver: Maneuver,
    move: int,
    posture: Posture,
    *,
    increased_dodge: bool = False,
) -> int:
    """B363-368: derive this declaration's maximum movement in yards."""
    permission = maneuver_permission(maneuver)
    movement = "half" if maneuver == "all_out_defense" and increased_dodge else permission.movement
    if movement in ("none", "triggered"):
        return 0
    if movement == "step":
        return 0 if posture in ("prone", "sitting") else step_allowance(move)
    available = posture_move_allowance(move, posture)
    return available // 2 if movement == "half" else available


def minimum_strength_penalty(minimum_st: int, current_st: int) -> int:
    """B270: one point of skill penalty per point below the weapon's minimum ST."""
    return max(0, minimum_st - current_st)


def strong_damage_bonus(dice: int) -> int:
    """B365: All-Out Attack (Strong), two damage or one per die, whichever is better."""
    return max(2, dice)


def weapon_target_penalty(reach: int) -> int:
    """B400: reach C, 1, and 2+ weapon targets."""
    return -5 if reach == 0 else -4 if reach == 1 else -3


def shield_cover_dr(dr: int, maximum_hp: int, divisor: Decimal) -> int:
    """B408/B484: shield DR plus a quarter of original HP, divided and rounded down."""
    return int((Decimal(dr) + Decimal(maximum_hp) / 4) / divisor)


def shield_defense_bonus(bonus: int, disabled_arm: bool) -> int:
    """B421/B484: retain the shield's reduced DB when its arm is crippled."""
    return max(0, bonus - int(disabled_arm))
