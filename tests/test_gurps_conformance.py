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
    assert data["baseline_id"] == "gurps-4e-2004-first-printing+errata-2007-01-26"

    sources = {source["id"]: source for source in data["sources"]}
    assert set(sources) == {
        "sjg:gurps-lite-4e-2004",
        "sjg:basic-set-characters-4e-2004",
        "sjg:basic-set-campaigns-4e-2004",
    }
    assert all(source["edition"] == "Fourth Edition" for source in sources.values())
    assert sources["sjg:gurps-lite-4e-2004"]["revision"] == "07/12/04"
    for source in data["sources"][1:]:
        assert source["printing"] == 1
        assert source["errata"][0]["revision"] == "2007-01-26"

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
    assert by_id["lite-success-basic-pass"]["expected"] == {
        "success": True,
        "margin": 0,
    }
    assert by_id["lite-success-basic-fail"]["expected"] == {
        "success": False,
        "margin": -2,
    }
    assert by_id["lite-secondary-basic-speed"]["expected"] == {"basic_speed": "5.75"}
    assert by_id["lite-secondary-basic-move"]["expected"] == {
        "basic_move": 5,
        "unit": "yard/second",
    }


def test_current_inventory_does_not_claim_gurps_certification() -> None:
    lite = [entry for entry in CAPABILITIES.values() if entry.lite_required]
    basic = [entry for entry in CAPABILITIES.values() if entry.basic_required]
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in lite)
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in basic)


class FixtureDice:
    def __init__(self, dice: list[int]) -> None:
        self.dice = iter(dice)

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        value = next(self.dice)
        assert 1 <= value <= exclusive_upper_bound
        return value - 1


def test_independent_examples_against_prototype_checks() -> None:
    from wayfarer.rules.checks import Outcome, success_check

    data = json.loads(FIXTURE.read_text())
    for case in data["cases"]:
        if case["capability_id"] not in {"gurps.check.success", "gurps.check.critical"}:
            continue
        result = success_check(
            case["input"]["target"],
            rng=FixtureDice(case["input"]["roll"]),
            rules_package="package:wayfarer-lite",
            rules_version="1.0.0",
        )
        expected = case["expected"]
        assert result.margin == expected["margin"], case["id"]
        if "outcome" in expected:
            assert result.outcome.value == expected["outcome"], case["id"]
        else:
            assert (result.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)) == expected[
                "success"
            ], case["id"]


def test_quick_contest_divergence_is_visible() -> None:
    from wayfarer.rules.checks import contest

    data = json.loads(FIXTURE.read_text())
    case = next(case for case in data["cases"] if case["id"] == "quick-contest-both-fail")
    result = contest(
        "first",
        case["input"]["first_target"],
        "second",
        case["input"]["second_target"],
        rng=FixtureDice(case["input"]["roll"]),
        rules_package="package:wayfarer-lite",
        rules_version="1.0.0",
    )
    assert result.winner == case["prototype_expected"]["winner"]
    assert result.winner != case["expected"]["winner"]
    assert capability(case["capability_id"]).status is not CoverageStatus.VERIFIED


@pytest.mark.parametrize("profile_id", ["unknown", "package:wayfarer-lite", "GURPS-LITE-4E-2004"])
def test_unknown_profile_rejected_even_without_requirements(profile_id: str) -> None:
    from wayfarer.rules.conformance import require_capabilities

    with pytest.raises(ValidationError, match="Unknown rules profile"):
        require_capabilities(profile_id, ())


def test_requirements_cannot_fall_back_to_another_profile() -> None:
    from wayfarer.rules.conformance import require_capabilities

    with pytest.raises(ValidationError, match="outside profile"):
        require_capabilities("gurps-lite-4e-2004", ("gurps.tactical.hex_movement",))
    with pytest.raises(ValidationError, match="Unknown rules capability"):
        require_capabilities("gurps-basic-set-4e-2004", ("gurps.invented",))
    for identifier in CAPABILITIES:
        with pytest.raises(ValidationError, match="not verified"):
            require_capabilities("gurps-basic-set-4e-2004", (identifier,))


def test_fixtures_belong_to_their_exact_profile() -> None:
    from wayfarer.rules.conformance import profile

    for case in json.loads(FIXTURE.read_text())["cases"]:
        selected = profile(case["profile"])
        assert case["source_id"] in selected.source_ids
        assert case["capability_id"] in selected.required_capabilities
        assert case["provenance"]
