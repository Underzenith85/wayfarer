"""Fail-closed evidence joins for issue #727's combat capability rollups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import READY_IMPLEMENTATIONS, load_source_ledgers
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus

ROOT = Path(".")
FIXTURE = ROOT / "tests/fixtures/gurps/combat-capability-certification.json"
EXPECTED = {
    "gurps.combat.active_defense",
    "gurps.combat.grappling",
    "gurps.combat.maneuvers",
    "gurps.combat.melee_attack",
    "gurps.combat.melee_weapon_skills",
    "gurps.combat.ranged_weapon_skills",
    "gurps.combat.technique_procedures",
    "gurps.combat.turn_timing",
    "gurps.combat.unarmed",
    "gurps.tactical.facing",
    "gurps.tactical.hex_movement",
    "gurps.tactical.visibility",
}
SOURCE_IDS = {
    "sjg:basic-set-characters-4e-2004": "characters-third",
    "sjg:basic-set-campaigns-4e-2004": "campaigns-fourth",
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
    families: list[Family]


def matrix() -> Matrix:
    return cast(Matrix, json.loads(FIXTURE.read_text()))


def test_matrix_has_exact_scope_and_independent_expected_outcomes() -> None:
    data = matrix()
    assert data["schema_version"] == 1
    assert data["profile"] == "gurps-basic-set-4e-2004"
    assert data["baseline"] == "gurps-4e-characters-3p-2008+campaigns-4p-2008"
    assert data["owner_issue"] == 727
    families = data["families"]
    assert {family["capability_id"] for family in families} == EXPECTED
    for family in families:
        variants = family["variants"]
        assert len(variants) >= 3, family["capability_id"]
        assert len({variant["id"] for variant in variants}) == len(variants)
        for variant in variants:
            assert variant["source_id"] in SOURCE_IDS
            assert variant["reference"]
            assert variant["expected"]
            assert variant["source_rows"]
            assert variant["runtime_paths"]
            assert variant["tests"]


def test_every_variant_joins_reviewed_source_rows_and_real_paths() -> None:
    data = matrix()
    source_rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    for family in data["families"]:
        for variant in family["variants"]:
            # A variant names its primary independently reviewed source, but a
            # cross-volume behavior may also join a row from the other selected
            # Basic Set volume (for example, a Characters skill and Campaigns
            # attack procedure).
            assert variant["source_id"] in SOURCE_IDS
            for row_id in variant["source_rows"]:
                row = source_rows[row_id]
                assert row.source_id in SOURCE_IDS.values(), (variant["id"], row_id)
                assert row.source_review == "reviewed", (variant["id"], row_id)
                assert row.implementation in READY_IMPLEMENTATIONS, (variant["id"], row_id)
            for runtime_path in variant["runtime_paths"]:
                assert (ROOT / runtime_path).is_file(), (variant["id"], runtime_path)
            for test_path in variant["tests"]:
                assert (ROOT / test_path.split("::", 1)[0]).is_file(), (
                    variant["id"],
                    test_path,
                )


def test_owner_inventory_rows_are_exact_and_ready() -> None:
    data = matrix()
    item_rows = {row.id: row for row in inventory(ROOT)}
    for family in data["families"]:
        capability_id = family["capability_id"]
        owners = family["owner_inventory_rows"]
        assert owners[0] == f"capability:{capability_id}"
        declared = CAPABILITIES[capability_id]
        assert declared.status is CoverageStatus.VERIFIED
        assert declared.owner_issue == 727
        for row_id in owners[1:]:
            row = item_rows[row_id]
            assert row.source_review == "reviewed", row_id
            assert row.implementation in READY_IMPLEMENTATIONS | {"contextual"}, row_id
            # ``blockers`` retains the historical issue lineage for mundane
            # inventory rows. Readiness is the reviewed implementation plus an
            # empty concrete gap list; the matrix transfers rollup ownership.
            assert not row.gaps, row_id
