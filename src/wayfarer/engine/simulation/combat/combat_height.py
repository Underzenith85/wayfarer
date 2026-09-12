"""B402-403 melee-height rules using exact inches and attacker-specific reach."""

from fractions import Fraction

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class HeightEffect(Record):
    attack_modifier: int = 0
    defender_modifier: int = 0


def defense_height(defender_height: Fraction, attacker_height: Fraction, *, reach: int = 1) -> int:
    """B403: reduce separation using this fighter's own reach, not the foe's."""
    if type(reach) is not int or reach < 0:
        raise ValidationError("Reach must be a nonnegative integer")
    delta = defender_height - attacker_height
    feet = max(Fraction(0), abs(delta) * 3 - max(0, reach - 1) * 3)
    magnitude = 0 if feet <= 2 else 1 if feet <= 3 else 2 if feet <= 4 else 3
    return magnitude if delta > 0 else -magnitude


def melee_height(
    attacker_height: Fraction,
    defender_height: Fraction,
    *,
    reach: int,
    location: HitLocation | None,
) -> HeightEffect:
    """Heights are yards. Reach only reduces the attacker's effective separation.

    Explicit targeting is required where the height band restricts body parts;
    random selection must not roll an unreachable part after spending resources.
    Odd leaning/jumping positions require their own authoritative action.
    """
    if type(reach) is not int or reach < 0:
        raise ValidationError("Reach must be a nonnegative integer")
    delta = attacker_height - defender_height
    feet = max(Fraction(0), abs(delta) * 3 - max(0, reach - 1) * 3)
    if feet > 6:
        raise ValidationError("Target exceeds vertical melee reach")
    high = delta > 0
    head = location in ("skull", "face", "left-eye", "right-eye")
    lower = location in ("left-leg", "right-leg", "left-foot", "right-foot")
    if feet > 3 and high and (lower or location == "random"):
        raise ValidationError("Upper fighter cannot reach lower legs or feet")
    if feet > 4 and not high and (head or location == "random"):
        raise ValidationError("Lower fighter cannot reach the head")
    if feet > 5 and not (head if high else lower):
        raise ValidationError("Height requires targeting head above or legs/feet below")
    # An attacker's long reach does not bring the defender closer (B403).
    defense = defense_height(defender_height, attacker_height)
    attack = 0
    if 1 < feet <= 5:
        if high:
            attack = -2 if lower else 1 if head or location == "neck" else 0
        else:
            attack = 2 if lower else -2 if head else 0
    return HeightEffect(attack_modifier=attack, defender_modifier=defense)
