"""B417 optional eligibility and exact selected-source boundary regressions (#744)."""

from dataclasses import replace
from pathlib import Path

import pytest

from wayfarer.certification.source_audit import inventory, load, validate
from wayfarer.certification.source_ledgers import (
    denominator_identity,
    load_source_ledgers,
    validate_source_ledgers,
)
from wayfarer.engine.rules.conformance import CAPABILITIES
from wayfarer.engine.rules.profiles import BASIC_SET_OPTIONAL_RULES, GURPS_PROPAGANDA_PROFILE
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
B417 = {
    "section:campaigns:b417:cinematic-combat-rules",
    "section:campaigns:b417:dual-weapon-attacks",
}


def test_b417_is_optional_and_cannot_borrow_armor_divisor_verification() -> None:
    rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    for identifier in B417:
        row = rows[identifier]
        assert row.printed_page == 417
        assert row.disposition == "optional-disabled"
        assert row.obligation == "profile-excluded"
        assert row.capability_id is None
        assert row.implementation == "not-applicable"
    selected = GURPS_PROPAGANDA_PROFILE
    assert "gurps.techniques.dual-weapon-attack" not in selected.optional_rules
    for identifier in BASIC_SET_OPTIONAL_RULES:
        with pytest.raises(ValidationError, match="is disabled"):
            selected.require_optional_rule(identifier)


@pytest.mark.parametrize("identifier", sorted(B417))
@pytest.mark.parametrize("change", ["required", "capability", "classification"])
def test_optional_section_conflicts_fail_the_source_gate(identifier: str, change: str) -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(row for row in bundle.rows if row.id == identifier)
    if change == "required":
        bad = row.model_copy(update={"disposition": "required", "implementation": "verified"})
    elif change == "capability":
        bad = row.model_copy(update={"capability_id": "gurps.injury.armor_divisors"})
    else:
        bad = row.model_copy(update={"classification": "unreviewed"})
    rows = tuple(bad if item.id == identifier else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="Optional source disposition drift"):
        validate_source_ledgers(
            replace(bundle, rows=rows), inventory(ROOT), frozenset(CAPABILITIES), ROOT
        )


def test_standard_magic_psi_and_selected_printings_are_included() -> None:
    manifest = load(ROOT)
    scope = next(scope for scope in manifest.scopes if scope.id == "basic-standard-magic-psi")
    assert scope.decision == "required" and scope.reviewed
    assert [
        (source.id, source.observed) for source in manifest.sources if "basic-set" in source.id
    ] == [
        ("sjg:basic-set-characters-4e-2004", "Third printing, February 2008"),
        ("sjg:basic-set-campaigns-4e-2004", "Fourth printing"),
    ]
    wrong = scope.model_copy(update={"decision": "optional-disabled"})
    changed = manifest.model_copy(
        update={"scopes": tuple(wrong if item.id == scope.id else item for item in manifest.scopes)}
    )
    with pytest.raises(ValidationError, match="magic/psi cannot be excluded"):
        validate(ROOT, changed)


def test_infinite_worlds_mechanics_are_explicitly_excluded_separately_from_setting() -> None:
    rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    with pytest.raises(ValidationError, match="Content is excluded"):
        GURPS_PROPAGANDA_PROFILE.require_content("gurps.content.infinite-worlds")
    # B529-531 procedures can be reused outside the setting, but are not enabled
    # by excluding only the setting prose. They need a separate reviewed profile.
    for identifier in (
        "section:campaigns:b529:interdimensional-travel",
        "section:campaigns:b530:parachronic-coordinates",
        "section:campaigns:b531:operations-and-accidents",
    ):
        row = rows[identifier]
        assert row.classification == "infinite-worlds-reusable-mechanic-excluded"
        assert row.disposition == "excluded"


def test_scope_reconciliation_changes_only_the_two_b417_membership_dispositions() -> None:
    bundle = load_source_ledgers(ROOT)
    previous = tuple(
        row.model_copy(update={"disposition": "required"}) if row.id in B417 else row
        for row in bundle.rows
    )
    assert denominator_identity(previous, inventory(ROOT)) == (
        "c3987f2b8a441413e7cde3bec05c74a9270fd3cbe2b1b9422434b7c7a322428e"
    )
    assert len(bundle.rows) == 1285
