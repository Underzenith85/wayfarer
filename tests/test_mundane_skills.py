"""Catalog identity, structural classes and fail-closed runtime availability."""

from dataclasses import replace

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import RulesCatalog
from wayfarer.rules.mundane_skills import (
    audit_report,
    candidate_package,
    inventory,
    require_available,
    validate_inventory,
)
from wayfarer.rules.skill_types import Difficulty


def test_inventory_and_references() -> None:
    entries = inventory()
    validate_inventory(entries)
    ids = {e.id for e in entries}
    assert {
        "skill:accounting",
        "skill:surgery",
        "skill:survival-woodlands",
        "skill:arm-lock-judo",
    } <= ids
    assert len(entries) > 180
    assert audit_report()["available"] == 0
    assert all(e.blockers and e.followup_issues for e in entries)
    RulesCatalog((candidate_package(),))
    assert candidate_package().digest == candidate_package().digest
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_inventory(entries + entries[:1])
    with pytest.raises(ValidationError, match="references"):
        validate_inventory(tuple(e for e in entries if e.id != "skill:karate"))


def test_numeric_metadata_and_structural_classes() -> None:
    entries = {e.id: e for e in inventory()}
    first_aid = entries["skill:first-aid"].definition
    assert first_aid is not None and first_aid.skill is not None
    assert first_aid.skill.difficulty is Difficulty.EASY
    assert first_aid.skill.defaults[0].modifier == -4
    intimidation = entries["skill:intimidation"].definition
    assert intimidation is not None and intimidation.skill is not None
    assert intimidation.skill.attribute == "secondary:will"
    specs = [e.definition.skill for e in entries.values() if e.definition and e.definition.skill]
    assert {s.difficulty for s in specs} == set(Difficulty)
    assert any(s.technique for s in specs)
    assert any(s.specialty and s.specialty.optional_parent for s in specs)
    assert any(s.specialty and s.specialty.optional_parent is None for s in specs)
    changed = replace(first_aid, name="changed")
    package = candidate_package()
    assert replace(package, definitions=(changed,)).digest != package.digest


@pytest.mark.parametrize("id", ["skill:first-aid", "skill:physics", "skill:invented-skill"])
def test_model_cannot_turn_inventory_into_available_mechanics(id: str) -> None:
    with pytest.raises(ValidationError):
        require_available(id)
