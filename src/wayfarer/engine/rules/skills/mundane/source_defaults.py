"""Load the source inventory's skill defaults without importing its bindings.

The procedure modules are imported by :mod:`wayfarer.engine.rules.skills.mundane`, so
they cannot import that package's inventory builder without a cycle.  Defaults
are source facts rather than procedure behavior; this small reader lets both
the package builder and an independently exposed procedure definition use the
same JSON record instead of maintaining a second, drifting table (#383).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import TypeAdapter

from wayfarer.engine.rules.skills.mundane.schema import (
    ContextualPrerequisiteRecord,
    DefaultConditionRecord,
    InventoryRow,
)
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute,
    DefaultCondition,
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
    Technique,
    TechniqueTemplate,
)

_ATTRIBUTES = {
    "IQ": ControllingAttribute.IQ,
    "DX": ControllingAttribute.DX,
    "HT": ControllingAttribute.HT,
    "ST": ControllingAttribute.ST,
    "Will": ControllingAttribute.WILL,
    "Per": ControllingAttribute.PER,
    "Perception": ControllingAttribute.PER,
}


@cache
def _rows() -> dict[str, InventoryRow]:
    path = Path(__file__).with_name("inventory.json")
    values = TypeAdapter(tuple[InventoryRow, ...]).validate_json(path.read_text())
    return {f"skill:{row.id}": row for row in values}


def _conditions(values: tuple[DefaultConditionRecord, ...]) -> tuple[DefaultCondition, ...]:
    return tuple(DefaultCondition(value.kind, value.value) for value in values)


def recorded_defaults(identifier: str) -> tuple[SkillDefault, ...]:
    """Return the exact defaults recorded for one source-indexed row."""
    row = _rows()[identifier]

    return tuple(
        SkillDefault(_ATTRIBUTES[value.attribute], value.modifier, _conditions(value.conditions))
        for value in row.attribute_defaults
    ) + tuple(
        SkillDefault(f"skill:{value.target}", value.modifier, _conditions(value.conditions))
        for value in row.skill_defaults
    )


def recorded_blockers(identifier: str) -> frozenset[str]:
    """Expose blocker names solely for procedure/source reconciliation."""
    return frozenset(_rows()[identifier].blockers)


def recorded_spec(identifier: str) -> SkillSpec | None:
    """Return one row's complete frozen skill specification.

    Runtime procedure catalogs own behavior, not a second copy of source data.
    Reading the already-pinned inventory here keeps their bindings exact while
    still avoiding an import cycle through the aggregate inventory builder.
    """
    row = _rows()[identifier]
    if row.attribute is None or row.difficulty is None:
        return None

    def prerequisite(value: str | ContextualPrerequisiteRecord) -> SkillPrerequisite:
        if isinstance(value, str):
            return SkillPrerequisite(f"skill:{value}")
        target = (
            f"skill:{value.target}"
            if value.kind is PrerequisiteKind.TRAINED_SKILL
            else value.target
        )
        return SkillPrerequisite(
            target,
            value.minimum,
            value.kind,
            value.minimum_technology_level,
        )

    return SkillSpec(
        _ATTRIBUTES[row.attribute],
        row.difficulty,
        f"B{row.page}",
        recorded_defaults(identifier),
        tuple(SkillPrerequisite(f"skill:{value}") for value in row.prerequisites)
        + tuple(prerequisite(value) for value in row.contextual_prerequisites),
        Specialty(
            row.specialty.family,
            row.specialty.name,
            f"skill:{row.specialty.optional_parent}" if row.specialty.optional_parent else None,
        )
        if row.specialty
        else None,
        Technique(
            f"skill:{row.technique.parent}",
            row.technique.default_modifier,
            row.technique.maximum_modifier,
        )
        if row.technique
        else None,
        tuple(
            PrerequisiteGroup(tuple(prerequisite(value) for value in group.alternatives))
            for group in row.prerequisite_groups
        ),
        row.tl_required,
    )


def recorded_template(identifier: str) -> TechniqueTemplate | None:
    """Return a dynamic technique template without making it a skill definition."""
    row = _rows()[identifier]
    if row.template is None:
        return None
    return TechniqueTemplate(
        row.template.difficulty,
        row.template.default_modifier,
        row.template.maximum_modifier,
        tuple(f"skill:{parent}" for parent in row.template.parents),
        f"skill:{row.template.parent_family}" if row.template.parent_family else None,
        _ATTRIBUTES[row.template.attribute] if row.template.attribute else None,
        row.template.optional_rule,
    )


def recorded_specialties(identifier: str) -> tuple[str, ...]:
    """Return the concrete children that complete a finite family row."""
    return tuple(f"skill:{value}" for value in _rows()[identifier].specialties)


def recorded_variable_subject(identifier: str) -> str | None:
    """Return the campaign-selected subject axis for an open family."""
    variable = _rows()[identifier].variable
    return variable.subject if variable else None


def recorded_reference(identifier: str) -> str:
    """Return the printed page reference even for a non-rollable family row."""
    return f"B{_rows()[identifier].page}"
