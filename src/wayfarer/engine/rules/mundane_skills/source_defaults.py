"""Load the source inventory's skill defaults without importing its bindings.

The procedure modules are imported by :mod:`wayfarer.engine.rules.mundane_skills`, so
they cannot import that package's inventory builder without a cycle.  Defaults
are source facts rather than procedure behavior; this small reader lets both
the package builder and an independently exposed procedure definition use the
same JSON record instead of maintaining a second, drifting table (#383).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import TypeAdapter

from wayfarer.engine.rules.mundane_skills.schema import DefaultConditionRecord, InventoryRow
from wayfarer.engine.rules.skill_types import (
    ControllingAttribute,
    DefaultCondition,
    SkillDefault,
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
