"""Exhaustive Basic Set imports are evidence ledgers, not runtime catalogs."""

from dataclasses import replace
from pathlib import Path

import pytest

from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import (
    EXPECTED_LEDGER_COUNTS,
    LedgerBundle,
    ledger_blockers,
    load_source_ledgers,
    reconcile_trait_ledger,
    validate_source_ledgers,
)
from wayfarer.engine.rules.conformance import CAPABILITIES
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def _validate(bundle: LedgerBundle) -> None:
    validate_source_ledgers(bundle, inventory(), frozenset(CAPABILITIES))


def test_selected_printing_ledgers_have_the_exhaustive_source_packet_denominator() -> None:
    bundle = load_source_ledgers(ROOT)
    assert {name: len(rows) for name, rows in bundle.by_type.items()} == EXPECTED_LEDGER_COUNTS
    assert len(bundle.rows) == 1_285
    assert sum(row.source_review == "reviewed" for row in bundle.rows) == 43
    assert len(ledger_blockers(bundle.rows)) == len(bundle.rows) - 43

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


def test_every_trait_row_has_separate_construction_consequence_and_review_ownership() -> None:
    bundle = load_source_ledgers(ROOT)
    reconciled = reconcile_trait_ledger(bundle.by_type["traits"], inventory())
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
    assert all(row.source_review_owner == 191 for row in bundle.by_type["traits"])

    spines = next(row for row in named if row.source_row_id == "trait:advantage:spines")
    assert spines.source_class == "exotic"
    assert spines.runtime_binding == "supernatural/advantage:spines"

    absent = next(row for row in named if row.source_row_id == "trait:advantage:absolute-direction")
    assert absent.construction == "source-value-recorded"
    assert not absent.available
    assert absent.consequence_owner == 514


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


def test_implemented_or_reviewed_dispositions_require_evidence() -> None:
    bundle = load_source_ledgers(ROOT)
    row = next(row for row in bundle.rows if row.disposition == "required")
    changed = row.model_copy(update={"implementation": "implemented", "source_review": "reviewed"})
    rows = tuple(changed if item.id == row.id else item for item in bundle.rows)
    with pytest.raises(ValidationError, match="reviewed evidence"):
        _validate(replace(bundle, rows=rows))


def test_certification_reports_stable_ledger_blockers_and_rollups() -> None:
    report = evaluate(ROOT)
    ledger = [blocker for blocker in report.blockers if blocker.kind == "ledger"]
    assert len(ledger) == 1_242
    assert all(
        blocker.identifier.startswith(("section:", "trait:", "modifier:")) for blocker in ledger
    )
    assert all(blocker.owner_issue is not None for blocker in ledger)
    assert report.source_ledger_rows == 1_285
    assert report.required_source_ledger_rows == 1_179
    assert report.source_ledger_rollups["source_review"] == {
        "pending": 1_242,
        "reviewed": 43,
    }
    assert report.source_ledger_rollups["completion_owner"]["496"] == 514
