"""Frozen GURPS scope and independent conformance-fixture contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Modifier, ModifierKind, RecordedDice
from wayfarer.rules.conformance import (
    BASELINE_ID,
    CAPABILITIES,
    CoverageStatus,
    capability,
    require_verified,
)
from wayfarer.rules.gurps_checks import (
    AttemptTrace,
    Contestant,
    RepeatedAttemptPolicy,
    quick_contest,
    regular_contest,
    repeated_attempt,
    resistance_roll,
    success_roll,
)

FIXTURE = Path("tests/fixtures/gurps/conformance.json")
CHECK_CAPABILITIES = {
    "gurps.check.success",
    "gurps.check.margin",
    "gurps.check.critical",
    "gurps.check.quick_contest",
    "gurps.check.regular_contest",
    "gurps.check.resistance",
}


def load_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = json.loads(FIXTURE.read_text())["cases"]
    return cases


def check_cases(service: str) -> list[dict[str, object]]:
    return [case for case in load_cases() if case.get("service") == service]


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
    entry = CAPABILITIES["gurps.character.self_control"]
    assert entry.status is CoverageStatus.ABSENT
    with pytest.raises(ValidationError, match="not verified"):
        require_verified(entry.id)


def test_verified_capabilities_belong_to_landed_mechanics_issues() -> None:
    verified = {
        entry.id for entry in CAPABILITIES.values() if entry.status is CoverageStatus.VERIFIED
    }
    assert verified == CHECK_CAPABILITIES | {
        "gurps.character.primary_attributes",
        "gurps.character.secondary_characteristics",
        "gurps.character.skill_difficulty",
        "gurps.character.skill_defaults",
        "gurps.character.specialties",
        "gurps.character.techniques",
    }
    assert all(CAPABILITIES[identifier].owner_issue in (97, 98, 99) for identifier in verified)


def test_conformance_fixture_contract_is_source_referenced_and_independent() -> None:
    data = json.loads(FIXTURE.read_text())
    assert data["schema_version"] == 1
    assert data["baseline_id"] == BASELINE_ID

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
        if case["capability_id"] in CHECK_CAPABILITIES:
            assert case["service"] in {
                "success",
                "repeated_attempt",
                "quick_contest",
                "regular_contest",
                "resistance",
            }, case["id"]

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
    assert by_id["quick-contest-both-fail"]["expected"] == {"winner": "first"}
    assert by_id["basic-rule-of-16-caps-attacker"]["expected"]["attacker_effective_target"] == 16
    assert by_id["basic-regular-contest-high-skill-levelling"]["expected"]["adjusted_targets"] == [
        14,
        12,
    ]


def test_every_verified_check_capability_has_independent_cases_in_both_evidence_profiles() -> None:
    cases = load_cases()
    for identifier in CHECK_CAPABILITIES:
        matching = [case for case in cases if case["capability_id"] == identifier]
        assert len(matching) >= 3, identifier
        assert all(case["provenance"] for case in matching)
        profiles = {str(case["profile"]) for case in matching}
        if CAPABILITIES[identifier].lite_required and identifier != "gurps.check.resistance":
            assert "gurps-lite-4e-2004" in profiles, identifier
        assert "gurps-basic-set-4e-2004" in profiles, identifier


def test_verified_capabilities_carry_executable_evidence() -> None:
    """A verified entry needs fixture cases for every profile that requires it."""

    data = json.loads(FIXTURE.read_text())
    covered = {(case["capability_id"], case["profile"]) for case in data["cases"]}
    for entry in CAPABILITIES.values():
        if entry.status is not CoverageStatus.VERIFIED:
            continue
        assert (entry.id, "gurps-basic-set-4e-2004") in covered, entry.id
        # The frozen Lite artifact has no resisted supernatural attacks to cite.
        if entry.lite_required and entry.id != "gurps.check.resistance":
            assert (entry.id, "gurps-lite-4e-2004") in covered, entry.id
    assert CAPABILITIES["gurps.character.size_modifier_costs"].status is CoverageStatus.ABSENT
    assert not CAPABILITIES["gurps.character.size_modifier_costs"].lite_required


def test_current_inventory_does_not_claim_gurps_certification() -> None:
    lite = [entry for entry in CAPABILITIES.values() if entry.lite_required]
    basic = [entry for entry in CAPABILITIES.values() if entry.basic_required]
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in lite)
    assert any(entry.status is not CoverageStatus.VERIFIED for entry in basic)


def _dice(case: Mapping[str, object], key: str = "roll") -> RecordedDice:
    values = case["input"]
    assert isinstance(values, dict)
    return RecordedDice(values[key])


def _input(case: Mapping[str, object]) -> dict[str, object]:
    values = case["input"]
    assert isinstance(values, dict)
    return dict(values)


def _expected(case: Mapping[str, object]) -> dict[str, object]:
    values = case["expected"]
    assert isinstance(values, dict)
    return dict(values)


def _modifiers(case: Mapping[str, object]) -> tuple[Modifier, ...]:
    raw = _input(case).get("modifiers", [])
    assert isinstance(raw, list)
    return tuple(
        Modifier(
            entry["value"],
            f"fixture:{index}",
            "fixture",
            "1",
            ModifierKind(entry["kind"]),
        )
        for index, entry in enumerate(raw)
    )


@pytest.mark.parametrize("case", check_cases("success"), ids=lambda case: str(case["id"]))
def test_success_cases_match_published_expectations(case: dict[str, object]) -> None:
    values = _input(case)
    target = values["target"]
    assert isinstance(target, int)
    dice = _dice(case)
    result = success_roll(str(case["profile"]), target, _modifiers(case), rng=dice)
    assert dice.exhausted()
    expected = _expected(case)
    if "margin" in expected:
        assert result.margin == expected["margin"]
    if "outcome" in expected:
        assert result.outcome.value == expected["outcome"]
    if "success" in expected:
        assert result.outcome.succeeded == expected["success"]
    if "effective_target" in expected:
        assert result.effective_target == expected["effective_target"]
    assert result.rules_package == case["profile"] and result.rules_version == BASELINE_ID
    assert result.rule_id == "gurps.check.success"


@pytest.mark.parametrize("case", check_cases("repeated_attempt"), ids=lambda case: str(case["id"]))
def test_repeated_attempt_cases_follow_declared_policy(case: dict[str, object]) -> None:
    values = _input(case)
    policy = RepeatedAttemptPolicy(str(values["policy"]))
    target = values["target"]
    attempts = values["attempts"]
    assert isinstance(target, int) and isinstance(attempts, list)
    profile = str(case["profile"])
    history: tuple[AttemptTrace, ...] = ()
    rejected = False
    for dice in attempts:
        try:
            history += (repeated_attempt(profile, policy, history, target, rng=RecordedDice(dice)),)
        except ValidationError:
            rejected = True
            break
    expected = _expected(case)
    assert len(history) == expected["attempts_resolved"]
    assert [attempt.attempt for attempt in history] == list(range(1, len(history) + 1))
    if "second_attempt" in expected:
        assert rejected and expected["second_attempt"] == "rejected"
    else:
        assert not rejected
    if "final_success" in expected:
        assert history[-1].check.outcome.succeeded == expected["final_success"]
    if "hazards" in expected:
        assert sum(attempt.hazard for attempt in history) == expected["hazards"]
    if "revealed" in expected:
        assert all(attempt.revealed == expected["revealed"] for attempt in history)


@pytest.mark.parametrize("case", check_cases("quick_contest"), ids=lambda case: str(case["id"]))
def test_quick_contest_cases_match_published_expectations(case: dict[str, object]) -> None:
    values = _input(case)
    first, second = values["first_target"], values["second_target"]
    assert isinstance(first, int) and isinstance(second, int)
    dice = _dice(case)
    result = quick_contest(
        str(case["profile"]), Contestant("first", first), Contestant("second", second), rng=dice
    )
    assert dice.exhausted()
    expected = _expected(case)
    assert result.winner == expected["winner"]
    if "decision" in expected:
        assert result.decision == expected["decision"]
    if "victory_margin" in expected:
        assert result.victory_margin == expected["victory_margin"]
    if "first_outcome" in expected:
        assert result.first.outcome.value == expected["first_outcome"]


@pytest.mark.parametrize("case", check_cases("regular_contest"), ids=lambda case: str(case["id"]))
def test_regular_contest_cases_match_published_expectations(case: dict[str, object]) -> None:
    values = _input(case)
    first, second = values["first_target"], values["second_target"]
    assert isinstance(first, int) and isinstance(second, int)
    dice = _dice(case)
    result = regular_contest(
        str(case["profile"]), Contestant("first", first), Contestant("second", second), rng=dice
    )
    assert dice.exhausted()
    expected = _expected(case)
    assert len(result.rounds) == expected["rounds"]
    assert result.winner == expected["winner"]
    adjusted = [result.rounds[0][0].effective_target, result.rounds[0][1].effective_target]
    assert adjusted == expected["adjusted_targets"]
    assert all(
        (a.effective_target, b.effective_target) == tuple(adjusted) for a, b in result.rounds
    )


@pytest.mark.parametrize("case", check_cases("resistance"), ids=lambda case: str(case["id"]))
def test_resistance_cases_match_published_expectations(case: dict[str, object]) -> None:
    values = _input(case)
    attacker, resister = values["attacker_target"], values["resister_target"]
    assert isinstance(attacker, int) and isinstance(resister, int)
    dice = _dice(case)
    result = resistance_roll(
        str(case["profile"]),
        Contestant("attacker", attacker),
        Contestant("resister", resister),
        rule_of_16=bool(values["rule_of_16"]),
        rng=dice,
    )
    assert dice.exhausted()
    expected = _expected(case)
    assert result.affected == expected["affected"]
    if "attacker_effective_target" in expected:
        assert result.attacker.effective_target == expected["attacker_effective_target"]
    if "attacker_margin" in expected:
        assert result.attacker.margin == expected["attacker_margin"]
    if "victory_margin" in expected:
        assert result.contest.victory_margin == expected["victory_margin"]
    if "decision" in expected:
        assert result.contest.decision == expected["decision"]
    capped = [m for m in result.attacker.modifiers if m.kind is ModifierKind.RULE_OF_16]
    assert bool(capped) == (bool(values["rule_of_16"]) and attacker > max(16, resister))


def test_quick_contest_both_fail_prototype_divergence_stays_recorded() -> None:
    from wayfarer.rules.checks import contest

    case = next(case for case in load_cases() if case["id"] == "quick-contest-both-fail")
    values = _input(case)
    first, second = values["first_target"], values["second_target"]
    assert isinstance(first, int) and isinstance(second, int)
    prototype = contest(
        "first",
        first,
        "second",
        second,
        rng=_dice(case),
        rules_package="package:wayfarer-lite",
        rules_version="1.0.0",
    )
    prototype_expected = case["prototype_expected"]
    assert isinstance(prototype_expected, dict)
    assert prototype.winner == prototype_expected["winner"]
    published = quick_contest(
        str(case["profile"]),
        Contestant("first", first),
        Contestant("second", second),
        rng=_dice(case),
    )
    assert published.winner == _expected(case)["winner"]
    assert published.winner != prototype.winner
    assert case["prototype_divergence"]


@pytest.mark.parametrize("profile_id", ["unknown", "package:wayfarer-lite", "GURPS-LITE-4E-2004"])
def test_unknown_profile_rejected_even_without_requirements(profile_id: str) -> None:
    from wayfarer.rules.conformance import require_capabilities

    with pytest.raises(ValidationError, match="Unknown rules profile"):
        require_capabilities(profile_id, ())


def test_requirements_cannot_fall_back_to_another_profile() -> None:
    from wayfarer.rules.conformance import require_capabilities

    with pytest.raises(ValidationError, match="outside profile"):
        require_capabilities("gurps-lite-4e-2004", ("gurps.tactical.hex_movement",))
    with pytest.raises(ValidationError, match="outside profile"):
        require_capabilities("gurps-lite-4e-2004", ("gurps.check.regular_contest",))
    with pytest.raises(ValidationError, match="Unknown rules capability"):
        require_capabilities("gurps-basic-set-4e-2004", ("gurps.invented",))
    for identifier, entry in CAPABILITIES.items():
        if entry.status is CoverageStatus.VERIFIED:
            require_capabilities("gurps-basic-set-4e-2004", (identifier,))
            continue
        with pytest.raises(ValidationError, match="not verified"):
            require_capabilities("gurps-basic-set-4e-2004", (identifier,))


def test_fixtures_belong_to_their_exact_profile() -> None:
    from wayfarer.rules.conformance import profile

    for case in load_cases():
        selected = profile(str(case["profile"]))
        assert case["source_id"] in selected.source_ids
        assert case["capability_id"] in selected.required_capabilities
        assert case["provenance"]
