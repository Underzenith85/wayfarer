"""Exhaustive Basic Set imports are evidence ledgers, not runtime catalogs."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import (
    EXPECTED_LEDGER_COUNTS,
    INFINITE_WORLDS_CLASSIFICATIONS,
    LedgerBundle,
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
    validate_source_ledgers(bundle, inventory(ROOT), frozenset(CAPABILITIES))


def test_selected_printing_ledgers_have_the_exhaustive_source_packet_denominator() -> None:
    bundle = load_source_ledgers(ROOT)
    assert {name: len(rows) for name, rows in bundle.by_type.items()} == EXPECTED_LEDGER_COUNTS
    assert len(bundle.rows) == 1_285
    assert all(row.source_review == "reviewed" for row in bundle.rows)
    assert len(ledger_blockers(bundle.rows)) == 914

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

    absent = next(row for row in named if row.source_row_id == "trait:advantage:absolute-direction")
    assert absent.construction == "source-value-recorded"
    assert not absent.available
    assert absent.consequence_owner == 496


def test_duplicate_ids_invalid_pages_and_missing_or_closed_owners_are_rejected() -> None:
    bundle = load_source_ledgers(ROOT)
    first, second, *tail = bundle.rows
    with pytest.raises(ValidationError, match="Duplicate source-ledger"):
        _validate(replace(bundle, rows=(first, second.model_copy(update={"id": first.id}), *tail)))

    bad_page = first.model_copy(update={"printed_page": 337})
    with pytest.raises(ValidationError, match="outside selected printing"):
        _validate(replace(bundle, rows=(bad_page, *bundle.rows[1:])))

    unowned = first.model_copy(update={"completion_owner": None})
    with pytest.raises(ValidationError, match="lacks completion owner"):
        _validate(replace(bundle, rows=(unowned, *bundle.rows[1:])))

    issue = next(issue for issue in bundle.owners.issues if issue.issue == first.completion_owner)
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
        validate_source_ledgers(bundle, (*inventory_rows, runtime), frozenset(CAPABILITIES))

    wrong_profile = replace(runtime, required_profiles=("gurps-lite-4e-2004",))
    wrong_profile_rows = list(inventory_rows)
    wrong_profile_rows[runtime_index] = wrong_profile
    with pytest.raises(ValidationError, match="outside profile"):
        validate_source_ledgers(bundle, tuple(wrong_profile_rows), frozenset(CAPABILITIES))

    unreviewed_inventory = list(inventory_rows)
    unreviewed_inventory[runtime_index] = replace(runtime, source_review="pending")
    with pytest.raises(ValidationError, match="review is not joined"):
        validate_source_ledgers(bundle, tuple(unreviewed_inventory), frozenset(CAPABILITIES))

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
        )


def test_implemented_or_reviewed_dispositions_require_evidence() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(row for row in bundle.rows if row.disposition == "required")
    changed = row.model_copy(
        update={
            "implementation": "implemented",
            "source_review": "reviewed",
            "evidence_paths": (),
        }
    )
    rows = tuple(changed if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="lacks evidence"):
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


def test_certification_reports_stable_ledger_blockers_and_rollups() -> None:
    report = evaluate(ROOT)
    ledger = [blocker for blocker in report.blockers if blocker.kind == "ledger"]
    assert len(ledger) == 914
    assert all(
        blocker.identifier.startswith(("section:", "trait:", "modifier:")) for blocker in ledger
    )
    assert all(blocker.owner_issue is not None for blocker in ledger)
    assert report.source_ledger_rows == 1_285
    assert report.required_source_ledger_rows == 1_178
    assert report.source_ledger_rollups["source_review"] == {"reviewed": 1_285}
    assert report.source_ledger_rollups["completion_owner"]["496"] == 513
