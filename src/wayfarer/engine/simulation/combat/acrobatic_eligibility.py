"""B345/B375: the preliminary skill check is separate from the Dodge roll."""

from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.simulation.combat.generations import acrobatic_skill_minimum_enabled
from wayfarer.errors import ValidationError


def require_skill_roll(skill: int, modifiers: tuple[Modifier, ...]) -> None:
    if acrobatic_skill_minimum_enabled() and skill + sum(m.value for m in modifiers) < 3:
        raise ValidationError("Acrobatic Dodge skill check requires effective skill at least 3")
