"""Basic Set certification must fail closed until every evidence dimension passes."""

from pathlib import Path

import pytest

from wayfarer.basic_set_certification import PROFILE_ID, evaluate, require_certified
from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import PROFILES

ROOT = Path(__file__).resolve().parents[1]


def test_basic_set_report_binds_exact_profile_and_source_baseline() -> None:
    result = evaluate(ROOT)
    assert result.profile_id == "profile:gurps-basic-set-4e-2004"
    assert result.profile_version >= 1
    assert len(result.profile_digest) == 64
    assert result.source_baseline == "gurps-4e-2004-first-printing+errata-2007-01-26"
    assert result.required_capabilities == len(PROFILES[PROFILE_ID].required_capabilities)
    assert 0 < result.verified_capabilities <= result.required_capabilities
    assert result.required_inventory_items > 0


def test_basic_set_gate_exposes_capability_source_and_inventory_blockers() -> None:
    result = evaluate(ROOT)
    assert result.certified is False
    kinds = {blocker.kind for blocker in result.blockers}
    assert {"source", "capability", "inventory"} <= kinds
    size = next(
        blocker
        for blocker in result.blockers
        if blocker.identifier == "gurps.character.size_modifier_costs"
    )
    assert size.owner_issue == 192
    assert any(blocker.identifier.startswith("source:") for blocker in result.blockers)
    assert any(
        blocker.owner_issue == 119 for blocker in result.blockers if blocker.kind == "inventory"
    )


def test_basic_set_release_rejects_current_incomplete_evidence() -> None:
    with pytest.raises(ValidationError, match="Basic Set certification blocked"):
        require_certified(ROOT)


def test_report_serialization_keeps_blockers_machine_readable() -> None:
    payload = evaluate(ROOT).as_dict()
    assert payload["certified"] is False
    blockers = payload["blockers"]
    assert isinstance(blockers, list)
    assert blockers
    for blocker in blockers:
        assert isinstance(blocker, dict)
        assert {"kind", "identifier", "detail", "owner_issue"} <= set(blocker)
