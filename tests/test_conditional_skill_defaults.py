"""Independent source fixtures for the completed #383 transcription."""

from __future__ import annotations

import json
from pathlib import Path

from wayfarer.engine.rules.mundane_skills import inventory

FIXTURE = Path(__file__).parent / "fixtures" / "gurps" / "conditional_skill_defaults.json"


def source_cases() -> dict[str, object]:
    value: object = json.loads(FIXTURE.read_text())
    assert isinstance(value, dict)
    return value


def test_representative_default_shapes_match_the_independent_source_fixture() -> None:
    fixture = source_cases()
    recorded = {entry.id: entry for entry in inventory()}
    cases = fixture["cases"]
    assert isinstance(cases, list)
    for case in cases:
        assert isinstance(case, dict)
        entry = recorded[str(case["id"])]
        assert entry.reference == case["reference"]
        assert entry.definition is not None and entry.definition.skill is not None
        actual = [
            [
                default.target,
                default.modifier,
                [condition.kind.value for condition in default.conditions],
            ]
            for default in entry.definition.skill.defaults
        ]
        expected = case["defaults"]
        assert isinstance(expected, list)
        if case.get("exact", True):
            assert actual == expected
        else:
            assert all(default in actual for default in expected)


def test_only_campaign_or_action_scoped_defaults_keep_a_followup() -> None:
    fixture = source_cases()
    expected = fixture["contextual_residuals"]
    assert isinstance(expected, dict)
    recorded = {entry.id: entry for entry in inventory()}
    for identifier, owners in expected.items():
        assert recorded[identifier].blocker_owners["contextual-default-procedure"] == tuple(owners)

    assert not [
        entry.id for entry in recorded.values() if "contextual-default-procedure" in entry.blockers
    ]

    assert not [
        entry
        for entry in recorded.values()
        if "conditional-or-skill-defaults" in entry.blockers
        or "weapon-default-audit" in entry.blockers
        or "prerequisite-procedure" in entry.blockers
    ]
