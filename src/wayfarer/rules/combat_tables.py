"""Stateless Basic Set combat formulas (B270, B365, B376, B400, B408, B484)."""

from decimal import Decimal


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
