"""Characters B168 technological skill penalties."""

from wayfarer.engine.rules.types.skill import ControllingAttribute
from wayfarer.errors import ValidationError


def technology_level_penalty(
    skill_tl: int, equipment_tl: int, attribute: ControllingAttribute
) -> int:
    """Use the asymmetric IQ table; other attributes lose one per TL."""
    difference = equipment_tl - skill_tl
    if attribute != ControllingAttribute.IQ:
        return -abs(difference)
    if difference >= 4:
        raise ValidationError("Equipment four or more TL above an IQ-based skill is impossible")
    if difference > 0:
        return -5 * difference
    return 2 * difference + 1 if difference < 0 else 0
