"""Exhaustive Basic Set imports are evidence ledgers, not runtime catalogs."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import (
    CAMPAIGNS_APPENDIX_REVIEW_IDS,
    CAMPAIGNS_SECTION_AUDIT_OWNER,
    EXPECTED_LEDGER_COUNTS,
    INFINITE_WORLDS_CLASSIFICATIONS,
    LedgerBundle,
    denominator_identity,
    ledger_blockers,
    load_source_ledgers,
    reconcile_trait_ledger,
    validate_source_ledgers,
)
from wayfarer.engine.rules.conformance import CAPABILITIES
from wayfarer.engine.rules.profiles import BASIC_SET_OPTIONAL_RULES
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def _validate(bundle: LedgerBundle) -> None:
    validate_source_ledgers(bundle, inventory(ROOT), frozenset(CAPABILITIES), ROOT)


def test_selected_printing_ledgers_have_the_exhaustive_source_packet_denominator() -> None:
    bundle = load_source_ledgers(ROOT)
    assert {name: len(rows) for name, rows in bundle.by_type.items()} == EXPECTED_LEDGER_COUNTS
    assert len(bundle.rows) == 1_285
    assert all(row.source_review == "reviewed" for row in bundle.rows)
    assert len(ledger_blockers(bundle.rows)) == 73

    optional = tuple(row for row in bundle.rows if row.disposition == "optional-disabled")
    assert len(optional) == 9
    assert all(
        row.row_kind == "optional-rule"
        and row.implementation == "unsupported"
        and row.completion_owner is None
        and row.listed_value == "disabled"
        for row in optional
    )
    assert {
        identifier for row in optional for identifier in (row.classification or "").split("|")
    } == set(BASIC_SET_OPTIONAL_RULES)

    infinite_worlds = tuple(
        row
        for row in bundle.rows
        if row.source_id == "campaigns-fourth" and 523 <= row.printed_page <= 546
    )
    assert len(infinite_worlds) == 53
    assert all(
        row.disposition == "excluded"
        and row.implementation == "not-applicable"
        and row.completion_owner is None
        and row.listed_value == "excluded-from-generic-profile"
        for row in infinite_worlds
    )
    assert Counter(row.classification for row in infinite_worlds) == {
        "infinite-worlds-setting-content": 30,
        "infinite-worlds-reusable-mechanic-excluded": 16,
        "infinite-worlds-separate-profile-content": 5,
        "infinite-worlds-reviewed-exclusion": 2,
    }
    assert set(row.classification for row in infinite_worlds) == set(
        INFINITE_WORLDS_CLASSIFICATIONS
    )

    traits = bundle.by_type["traits"]
    combat_reflexes = next(row for row in traits if row.id == "trait:advantage:combat-reflexes")
    assert (combat_reflexes.printed_page, combat_reflexes.list_page) == (43, 297)
    assert combat_reflexes.runtime_binding == "trait:combat-reflexes"
    assert sum(row.row_kind == "catalog-item" for row in traits) == 480
    assert sum(row.row_kind == "rollup" for row in traits) == 7

    modifiers = bundle.by_type["modifiers"]
    armor_divisors = [row for row in modifiers if row.title == "Armor Divisor"]
    assert {row.classification for row in armor_divisors} == {"enhancement", "limitation"}
    assert len({row.id for row in armor_divisors}) == 2

    sections = bundle.by_type["sections"]
    assert (
        next(row for row in sections if "influencing-success-rolls" in row.id).printed_page == 347
    )
    assert (
        next(row for row in sections if "optional-rules-for-injury" in row.id).printed_page == 420
    )


def test_campaigns_section_audit_has_exact_reviewed_obligations_and_bounded_residuals() -> None:
    bundle = load_source_ledgers(ROOT)
    rows = tuple(
        row
        for row in bundle.by_type["sections"]
        if CAMPAIGNS_SECTION_AUDIT_OWNER in row.historical_owners
    )
    assert len(rows) == 119
    assert Counter(row.classification for row in rows) == {
        "executable-mechanic": 68,
        "reference-only-guidance": 34,
        "construction-reference-data": 10,
        "structural-non-runtime": 7,
    }
    assert Counter(row.implementation for row in rows) == {
        "verified": 57,
        "not-applicable": 51,
        "absent": 11,
    }
    assert Counter(row.completion_owner for row in rows if row.completion_owner) == {
        686: 3,
        689: 6,
        690: 2,
    }
    assert all(row.completion_owner != 94 for row in rows)

    appendix = tuple(row for row in rows if row.id in CAMPAIGNS_APPENDIX_REVIEW_IDS)
    assert len(appendix) == 12
    assert Counter(row.classification for row in appendix) == {
        "executable-mechanic": 6,
        "construction-reference-data": 5,
        "structural-non-runtime": 1,
    }
    assert all(row.disposition != "setting-unresolved" for row in appendix)


def test_every_trait_row_has_separate_construction_consequence_and_review_ownership() -> None:
    bundle = load_source_ledgers(ROOT)
    reconciled = reconcile_trait_ledger(bundle.by_type["traits"], inventory(ROOT))
    assert len(reconciled) == 487
    named = [row for row in reconciled if row.trait_kind != "rollup"]
    assert len(named) == 480
    assert {row.trait_kind for row in named} == {
        "advantage",
        "disadvantage",
        "perk",
        "quirk",
    }
    assert all(row.cost_owner and row.consequence_owner for row in named)
    assert all(row.runtime_binding for row in named)
    assert all(row.source_review_owner is None for row in bundle.by_type["traits"])

    spines = next(row for row in named if row.source_row_id == "trait:advantage:spines")
    assert spines.source_class == "exotic"
    assert spines.runtime_binding == "supernatural/advantage:spines"

    completed = next(
        row for row in named if row.source_row_id == "trait:advantage:absolute-direction"
    )
    assert completed.construction == "source-value-recorded"
    assert completed.available
    assert completed.consequence_owner == 680


def test_duplicate_ids_invalid_pages_and_missing_or_closed_owners_are_rejected() -> None:
    bundle = load_source_ledgers(ROOT)
    first, second, *tail = bundle.rows
    with pytest.raises(ValidationError, match="Duplicate source-ledger"):
        _validate(replace(bundle, rows=(first, second.model_copy(update={"id": first.id}), *tail)))

    bad_page = first.model_copy(update={"printed_page": 337})
    with pytest.raises(ValidationError, match="outside selected printing"):
        _validate(replace(bundle, rows=(bad_page, *bundle.rows[1:])))

    blocker_index = next(
        index for index, row in enumerate(bundle.rows) if row.completion_owner is not None
    )
    blocker = bundle.rows[blocker_index]
    unowned = blocker.model_copy(update={"completion_owner": None})
    unowned_rows = list(bundle.rows)
    unowned_rows[blocker_index] = unowned
    with pytest.raises(ValidationError, match="lacks completion owner"):
        _validate(replace(bundle, rows=tuple(unowned_rows)))

    issue = next(issue for issue in bundle.owners.issues if issue.issue == blocker.completion_owner)
    closed = issue.model_copy(update={"state": "closed"})
    owners = bundle.owners.model_copy(
        update={
            "issues": tuple(
                closed if item.issue == issue.issue else item for item in bundle.owners.issues
            )
        }
    )
    with pytest.raises(ValidationError, match="missing or closed"):
        _validate(replace(bundle, owners=owners))


def test_runtime_ownership_and_denominator_drift_are_rejected() -> None:
    bundle = load_source_ledgers(ROOT)
    bound = next(row for row in bundle.rows if row.runtime_binding is not None)
    other_index = next(
        index
        for index, row in enumerate(bundle.rows)
        if row.id != bound.id and row.runtime_binding is None
    )
    rows = list(bundle.rows)
    rows[other_index] = rows[other_index].model_copy(
        update={"runtime_binding": bound.runtime_binding}
    )
    with pytest.raises(ValidationError, match="Duplicate runtime inventory ownership"):
        _validate(replace(bundle, rows=tuple(rows)))

    denominator = bundle.denominator.model_copy(update={"legacy_inventory_count": 0})
    with pytest.raises(ValidationError, match="catalog denominator drift"):
        _validate(replace(bundle, denominator=denominator))


def test_runtime_inventory_joins_are_unique_profile_scoped_and_review_aware() -> None:
    bundle = load_source_ledgers(ROOT)
    inventory_rows = inventory()
    bound = next(row for row in bundle.rows if row.runtime_binding is not None)
    runtime_index = next(
        index for index, row in enumerate(inventory_rows) if row.id == bound.runtime_binding
    )
    runtime = inventory_rows[runtime_index]

    with pytest.raises(ValidationError, match="Duplicate runtime inventory identifier"):
        validate_source_ledgers(bundle, (*inventory_rows, runtime), frozenset(CAPABILITIES), ROOT)

    wrong_profile = replace(runtime, required_profiles=("gurps-lite-4e-2004",))
    wrong_profile_rows = list(inventory_rows)
    wrong_profile_rows[runtime_index] = wrong_profile
    with pytest.raises(ValidationError, match="outside profile"):
        validate_source_ledgers(bundle, tuple(wrong_profile_rows), frozenset(CAPABILITIES), ROOT)

    unreviewed_inventory = list(inventory_rows)
    unreviewed_inventory[runtime_index] = replace(runtime, source_review="pending")
    with pytest.raises(ValidationError, match="review is not joined"):
        validate_source_ledgers(bundle, tuple(unreviewed_inventory), frozenset(CAPABILITIES), ROOT)

    reviewed_runtime = replace(runtime, source_review="reviewed", implementation="unsupported")
    reviewed_inventory = list(inventory_rows)
    reviewed_inventory[runtime_index] = reviewed_runtime
    implemented = bound.model_copy(update={"implementation": "implemented"})
    implemented_rows = tuple(implemented if row.id == bound.id else row for row in bundle.rows)
    with pytest.raises(ValidationError, match="implementation disagrees"):
        validate_source_ledgers(
            replace(bundle, rows=implemented_rows),
            tuple(reviewed_inventory),
            frozenset(CAPABILITIES),
            ROOT,
        )


def test_implemented_or_reviewed_dispositions_require_evidence() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(
        row
        for row in bundle.rows
        if row.disposition == "required" and row.implementation not in {"implemented", "verified"}
    )
    changed = row.model_copy(update={"evidence_paths": ()})
    rows = tuple(changed if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="lacks evidence"):
        _validate(replace(bundle, rows=rows))


def test_reviewed_runtime_and_source_implementation_are_bidirectionally_joined() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(row for row in bundle.rows if row.id == "trait:advantage:combat-reflexes")
    assert row.implementation == "implemented"
    downgraded = row.model_copy(update={"implementation": "partial", "completion_owner": 94})
    rows = tuple(downgraded if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="Source-ledger implementation is not joined"):
        _validate(replace(bundle, rows=rows))

    undocumented = row.model_copy(
        update={"evidence_paths": ("docs/gurps-basic-set-source-review.md",)}
    )
    rows = tuple(undocumented if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="lacks executable evidence"):
        _validate(replace(bundle, rows=rows))


def test_infinite_worlds_boundary_drift_is_rejected() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(
        row
        for row in bundle.rows
        if row.source_id == "campaigns-fourth" and 523 <= row.printed_page <= 546
    )
    changed = row.model_copy(update={"disposition": "setting-unresolved", "completion_owner": 94})
    rows = tuple(changed if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="Infinite Worlds boundary disposition drift"):
        _validate(replace(bundle, rows=rows))


def test_campaigns_section_obligations_cannot_fall_back_to_the_roadmap() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(
        row
        for row in bundle.rows
        if CAMPAIGNS_SECTION_AUDIT_OWNER in row.historical_owners
        and row.completion_owner is not None
    )
    changed = row.model_copy(update={"completion_owner": 94})
    rows = tuple(changed if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="falls back to roadmap"):
        _validate(replace(bundle, rows=rows))


def test_certification_reports_stable_ledger_blockers_and_rollups() -> None:
    report = evaluate(ROOT)
    ledger = [blocker for blocker in report.blockers if blocker.kind == "ledger"]
    assert len(ledger) == 73
    assert all(
        blocker.identifier.startswith(("section:", "trait:", "modifier:")) for blocker in ledger
    )
    assert all(blocker.owner_issue is not None for blocker in ledger)
    assert report.source_ledger_rows == 1_285
    assert report.required_source_ledger_rows == 1_046
    assert report.source_ledger_rollups["source_review"] == {"reviewed": 1_285}
    assert report.source_ledger_rollups["completion_owner"] == {
        "682": 2,
        "683": 1,
        "684": 2,
        "685": 3,
        "686": 5,
        "689": 6,
        "690": 2,
        "691": 6,
        "693": 1,
        "700": 10,
        "94": 35,
        "none": 1_212,
    }


def test_characters_section_obligations_are_explicit_and_bounded() -> None:
    bundle = load_source_ledgers(ROOT)
    reviewed = tuple(row for row in bundle.rows if row.obligation_review_issue == 678)
    assert len(reviewed) == 161
    assert Counter(row.obligation for row in reviewed) == {
        "construction-catalog": 67,
        "executable-mechanic": 68,
        "reference-only": 15,
        "structural-non-runtime": 11,
    }
    assert not any(row.completion_owner == 94 for row in reviewed)
    unresolved = tuple(row for row in reviewed if row.completion_owner is not None)
    assert len(unresolved) == 27
    assert Counter(row.completion_owner for row in unresolved) == {
        682: 2,
        683: 1,
        684: 2,
        685: 3,
        686: 2,
        691: 6,
        693: 1,
        700: 10,
    }
    assert all(row.obligation == "executable-mechanic" for row in unresolved)
    assert all(
        row.disposition == "reference-only"
        and row.implementation == "not-applicable"
        and row.completion_owner is None
        for row in reviewed
        if row.obligation in {"construction-catalog", "reference-only", "structural-non-runtime"}
    )
    assert denominator_identity(bundle.rows, inventory(ROOT)) == (
        "bb5504331c4518f2c3ad213f64627aaba2f2ff8c311e42ef1d80133066ff7390"
    )


def test_characters_section_obligation_drift_is_rejected() -> None:
    bundle = load_source_ledgers(ROOT)
    index = next(i for i, row in enumerate(bundle.rows) if row.obligation_review_issue == 678)
    rows = list(bundle.rows)
    rows[index] = rows[index].model_copy(update={"obligation": None})
    with pytest.raises(ValidationError, match="explicit obligation"):
        _validate(replace(bundle, rows=tuple(rows)))

    index = next(
        i
        for i, row in enumerate(bundle.rows)
        if row.obligation_review_issue == 678 and row.completion_owner is not None
    )
    rows = list(bundle.rows)
    rows[index] = rows[index].model_copy(update={"completion_owner": 94})
    with pytest.raises(ValidationError, match="falls back to roadmap"):
        _validate(replace(bundle, rows=tuple(rows)))
