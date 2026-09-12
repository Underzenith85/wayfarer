"""Trusted adapters from approved character builds to technology procedures."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.skills.mundane.technology.attempts import Operator
from wayfarer.errors import ValidationError


def operator_from_build(build: ValidatedBuild, skill_id: str) -> Operator:
    """Construct an operator from an approved purchase, never request TL integers."""
    purchase = next((p for p in build.purchases if p.definition_id == skill_id), None)
    if purchase is None or purchase.technology_level is None:
        raise ValidationError(f"Approved build has no TL purchase for {skill_id}")
    value = next((v.value for v in build.sheet.values if v.target == skill_id), None)
    if value is None or value != value.to_integral_value():
        raise ValidationError(f"Approved build has no whole skill level for {skill_id}")
    selected = frozenset(p.definition_id for p in build.purchases)
    capabilities = (
        ({"flight"} if "advantage:flight" in selected else set())
        | ({"aquatic"} if selected & {"advantage:amphibious", "disadvantage:aquatic"} else set())
        | (
            {"literacy"}
            if any(
                identifier.startswith("trait:language-") and identifier.endswith("-written")
                for identifier in selected
            )
            else set()
        )
    )
    return Operator(
        skill_id,
        int(value),
        purchase.technology_level,
        frozenset(identifier for identifier in selected if identifier.startswith("skill:")),
        None,
        selected,
        frozenset(capabilities),
    )
