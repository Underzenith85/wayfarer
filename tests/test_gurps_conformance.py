"""Frozen GURPS scope and independent conformance-fixture contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import (
    CAPABILITIES,
    CoverageStatus,
    capability,
    require_verified,
)

FIXTURE = Path("tests/fixtures/gurps/conformance.json")


def test_capability_inventory_is_stable_and_owned() -> None:
    assert CAPABILITIES
    assert all(identifier.startswith("gurps.") for identifier in CAPABILITIES)
    assert all(entry.id == identifier for identifier, entry in CAPABILITIES.items())
    assert all(entry.lite_required or entry.basic_required for entry in CAPABILITIES.values())
    assert all(entry.basic_required for entry in CAPABILITIES.values() if entry.lite_required)
    assert all(entry.owner_issue is not None for entry in CAPABILITIES.values())


def test_unknown_capability_fails_closed() -> None:
    with pytest.raises(ValidationError, match="Unknown rules capability"):
        capability("gurps.check.not-a-real-capability")


def test_unverified_capability_fails_closed() -> None:
    assert CAPABILITIES["gurps.check.success"].status is CoverageStatus.PARTIAL
    with pytest.raises(ValidationError, match="not verified"):
        require_verified("gurps.check.success")


def test_conformance_fixture_contract_is_source_referenced_and_independent() -> None:
    data = json.loads(FIXTURE.read_text())
    assert data["schema_version"] == 1
    assert data["baseline_id"] == "gurps-4e-legacy-2004+errata-2026-09-06"

    sources = {source["id"]: source for source in data["sources"]}
    assert set(sources) == {
        "sjg:gurps-lite-4e-2004",
        "sjg:basic-set-characters-4e-2004",
        "sjg:basic-set-campaigns-4e-2004",
    }
    assert all(source["edition"] == "Fourth Edition" for source in sources.values())
    assert all(source["errata_through"] == "2026-09-06" for source in sources.values())
    assert all(source["printing"] is None for source in sources.values())

    cases = data["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["capability_id"] in CAPABILITIES
        assert case["source_id"] in sources
        assert case["reference"]
        assert case["rounding"] in data["rounding_modes"]
        assert "input" in case and "expected" in case

    # These values are deliberately pinned in the fixture rather than calculated
    # by the implementation under test.
    by_id = {case["id"]: case for case in cases}
    assert by_id["lite-success-basic-pass"]["expected"] == {"success": True, "margin": 0}
    assert by_id["lite-success-basic-fail"]["expected"] == {"success": False, "margin": -2}
    assert by_id["lite-secondary-basic-speed"]["expected"] == {"basic_speed": "5.75"}
    assert by_id["lite-secondary-basic-move"]["expected"] == {
        "basic_move": 5,
        "unit": "yard/second",
    }
    assert by_id["basic-unknown-mechanic-must-not-fallback"]["expected"] == {
        "accepted": False,
        "error": "unknown-capability",
    }


def test_current_inventory_does_not_claim_gurps_certification() -> None:
    lite = [entry for entry in CAPABILITIES.values() if entry.lite_required]
    basic = [entry for entry in CAPABILITIES.values() if entry.basic_required]
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in lite)
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in basic)
