"""Fail-closed evidence joins for issue #726's character/social rollups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import (
    READY_IMPLEMENTATIONS,
    SOURCE_DIGESTS,
    load_source_ledgers,
)
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.skills.mundane import inventory as skill_inventory
from wayfarer.engine.rules.traits.mundane import inventory as trait_inventory

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gurps/character-social-capability-certification.json"
EXPECTED = {
    "gurps.character.development",
    "gurps.character.self_control",
    "gurps.character.traits",
    "gurps.social.fright",
    "gurps.social.influence",
    "gurps.social.reaction",
    "gurps.social.skill_procedures",
}
DEVELOPMENT_ROWS = {
    "development:adventure",
    "development:gained-in-play",
    "development:quick-learning",
    "development:study",
    "development:teachers",
    "development:learnable-advantages",
}


class Variant(TypedDict):
    id: str
    source_id: str
    reference: str
    source_rows: list[str]
    expected: dict[str, object]
    runtime_paths: list[str]
    tests: list[str]


class Family(TypedDict):
    capability_id: str
    owner_inventory_rows: list[str]
    variants: list[Variant]


class Matrix(TypedDict):
    schema_version: int
    profile: str
    baseline: str
    owner_issue: int
    source_sha256: dict[str, str]
    families: list[Family]


def matrix() -> Matrix:
    return cast(Matrix, json.loads(FIXTURE.read_text()))


def test_matrix_pins_exact_scope_sources_and_independent_outcomes() -> None:
    data = matrix()
    assert data["schema_version"] == 1
    assert data["profile"] == "gurps-basic-set-4e-2004"
    assert data["baseline"] == "gurps-4e-characters-3p-2008+campaigns-4p-2008"
    assert data["owner_issue"] == 726
    assert data["source_sha256"] == SOURCE_DIGESTS
    assert {family["capability_id"] for family in data["families"]} == EXPECTED
    for family in data["families"]:
        variants = family["variants"]
        assert len(variants) >= 3, family["capability_id"]
        assert len({variant["id"] for variant in variants}) == len(variants)
        for variant in variants:
            assert variant["source_id"] in SOURCE_DIGESTS
            assert variant["reference"]
            assert variant["expected"]
            assert variant["source_rows"]
            assert variant["runtime_paths"]
            assert variant["tests"]


def test_every_variant_joins_reviewed_source_rows_and_real_execution_paths() -> None:
    source_rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    for family in matrix()["families"]:
        for variant in family["variants"]:
            for row_id in variant["source_rows"]:
                row = source_rows[row_id]
                assert row.source_id in SOURCE_DIGESTS, (variant["id"], row_id)
                assert row.source_review == "reviewed", (variant["id"], row_id)
                assert row.implementation in READY_IMPLEMENTATIONS | {"not-applicable"}, (
                    variant["id"],
                    row_id,
                )
            for runtime_path in variant["runtime_paths"]:
                assert (ROOT / runtime_path).is_file(), (variant["id"], runtime_path)
            for test_path in variant["tests"]:
                assert (ROOT / test_path.split("::", 1)[0]).is_file(), (
                    variant["id"],
                    test_path,
                )


def test_owner_inventory_rows_are_exact_ready_and_promoted() -> None:
    item_rows = {row.id: row for row in inventory(ROOT)}
    for family in matrix()["families"]:
        capability_id = family["capability_id"]
        owners = family["owner_inventory_rows"]
        assert owners[0] == f"capability:{capability_id}"
        declared = CAPABILITIES[capability_id]
        assert declared.status is CoverageStatus.VERIFIED
        assert declared.owner_issue == 726
        for row_id in owners[1:]:
            row = item_rows[row_id]
            assert row.source_review == "reviewed", row_id
            assert row.implementation in READY_IMPLEMENTATIONS | {"contextual"}, row_id
            # ``blockers`` preserves historical issue lineage for catalog rows.
            # Readiness is the reviewed implementation plus no concrete gaps;
            # this matrix transfers the capability rollup to #726.
            assert not row.gaps, row_id


def test_complete_trait_and_self_control_ledgers_are_ready() -> None:
    source_rows = load_source_ledgers(ROOT).rows
    traits = [row for row in source_rows if row.capability_id == "gurps.character.traits"]
    assert len(traits) == 557
    assert sum(row.disposition == "required" for row in traits) == 526
    assert all(row.source_review == "reviewed" for row in traits)
    assert all(
        row.implementation in READY_IMPLEMENTATIONS
        if row.disposition == "required"
        else row.implementation == "not-applicable"
        for row in traits
    )

    self_control = [row for row in trait_inventory() if row.self_control]
    assert len(self_control) == 38
    audited = {row.id: row for row in inventory(ROOT)}
    for trait in self_control:
        row = audited[trait.id]
        assert row.source_review == "reviewed"
        assert row.implementation in READY_IMPLEMENTATIONS
        assert not row.gaps


def test_development_and_social_procedure_owner_sets_are_complete() -> None:
    audited = {row.id: row for row in inventory(ROOT)}
    assert all(
        audited[row_id].implementation in READY_IMPLEMENTATIONS
        and audited[row_id].source_review == "reviewed"
        and not audited[row_id].gaps
        and not audited[row_id].blockers
        for row_id in DEVELOPMENT_ROWS
    )

    social = [row for row in skill_inventory() if row.procedure_owner == 345]
    assert len(social) == 26
    assert all(row.bound and row.implementation == "implemented" for row in social)
    assert all(not row.blockers for row in social)


def test_selected_social_source_rows_are_reviewed_and_executable() -> None:
    source_rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    selected = {
        "section:campaigns:b359:influence-rolls",
        "section:campaigns:b360:will-rolls",
        "section:campaigns:b360:fright-checks",
        "section:campaigns:b360:fright-check-table",
        "section:campaigns:b494:reaction-rolls",
        "section:campaigns:b559:npc-reactions",
        "section:campaigns:b560:reaction-table",
    }
    assert all(source_rows[row_id].source_review == "reviewed" for row_id in selected)
    assert all(source_rows[row_id].implementation in READY_IMPLEMENTATIONS for row_id in selected)
