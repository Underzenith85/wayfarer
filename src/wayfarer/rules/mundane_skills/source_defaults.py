"""Load the source inventory's skill defaults without importing its bindings.

The procedure modules are imported by :mod:`wayfarer.rules.mundane_skills`, so
they cannot import that package's inventory builder without a cycle.  Defaults
are source facts rather than procedure behavior; this small reader lets both
the package builder and an independently exposed procedure definition use the
same JSON record instead of maintaining a second, drifting table (#383).
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from wayfarer.rules.skill_types import (
    ControllingAttribute,
    DefaultCondition,
    DefaultConditionKind,
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
def _rows() -> dict[str, dict[str, Any]]:
    path = Path(__file__).with_name("inventory.json")
    values: list[dict[str, Any]] = json.loads(path.read_text())
    return {f"skill:{row['id']}": row for row in values}


def recorded_defaults(identifier: str) -> tuple[SkillDefault, ...]:
    """Return the exact defaults recorded for one source-indexed row."""
    row = _rows()[identifier]

    def conditions(value: dict[str, Any]) -> tuple[DefaultCondition, ...]:
        return tuple(
            DefaultCondition(DefaultConditionKind(item["kind"]), item.get("value"))
            for item in value.get("conditions", ())
        )

    return tuple(
        SkillDefault(_ATTRIBUTES[value["attribute"]], value["modifier"], conditions(value))
        for value in row.get("attribute_defaults", ())
    ) + tuple(
        SkillDefault(f"skill:{value['target']}", value["modifier"], conditions(value))
        for value in row.get("skill_defaults", ())
    )


def recorded_blockers(identifier: str) -> frozenset[str]:
    """Expose blocker names solely for procedure/source reconciliation."""
    return frozenset(_rows()[identifier].get("blockers", ()))
