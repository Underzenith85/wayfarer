"""Fail-closed evidence joins for issue #728's engine capability rollups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from wayfarer.certification.source_audit import inventory
from wayfarer.certification.source_ledgers import READY_IMPLEMENTATIONS, load_source_ledgers
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gurps/equipment-injury-recovery-certification.json"
EXPECTED = {
    "gurps.equipment.armor_profiles",
    "gurps.equipment.catalog",
    "gurps.equipment.object_durability",
    "gurps.equipment.weapon_profiles",
    "gurps.injury.armor_divisors",
    "gurps.injury.damage_resistance",
    "gurps.injury.damage_types",
    "gurps.injury.hit_locations",
    "gurps.injury.hp_thresholds",
    "gurps.injury.lasting_wounds",
    "gurps.recovery.fatigue",
    "gurps.recovery.healing",
    "gurps.recovery.medical_treatment",
}
SOURCE_IDS = {
    "sjg:basic-set-characters-4e-2004": "characters-third",
    "sjg:basic-set-campaigns-4e-2004": "campaigns-fourth",
}
EXPECTED_SCOPE_COUNTS = {
    "equipment-catalog": 461,
    "equipment-sections": 14,
    "equipment-footnotes": 75,
    "equipment-field-provenance": 186,
    "equipment-package-binding": 1,
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
    assert data["owner_issue"] == 728
    assert {family["capability_id"] for family in data["families"]} == EXPECTED
    for family in data["families"]:
        assert family["owner_inventory_rows"][0] == (f"capability:{family['capability_id']}")
        variants = family["variants"]
        assert len(variants) >= 3, family["capability_id"]
        assert len({variant["id"] for variant in variants}) == len(variants)
        for variant in variants:
            assert variant["source_id"] in SOURCE_IDS
            assert variant["reference"] and variant["expected"]
            assert variant["source_rows"] and variant["runtime_paths"] and variant["tests"]


def test_every_variant_joins_reviewed_source_rows_and_named_executable_tests() -> None:
    source_rows = {row.id: row for row in load_source_ledgers(ROOT).rows}
    for family in matrix()["families"]:
        for variant in family["variants"]:
            for row_id in variant["source_rows"]:
                row = source_rows[row_id]
                assert row.source_id in SOURCE_IDS.values(), (variant["id"], row_id)
                assert row.source_review == "reviewed", (variant["id"], row_id)
                assert row.implementation in READY_IMPLEMENTATIONS, (variant["id"], row_id)
            for runtime_path in variant["runtime_paths"]:
                assert (ROOT / runtime_path).is_file(), (variant["id"], runtime_path)
            for binding in variant["tests"]:
                filename, separator, function = binding.partition("::")
                path = ROOT / filename
                assert separator and path.is_file(), (variant["id"], binding)
                assert f"def {function}(" in path.read_text(), (variant["id"], binding)


def test_catalog_family_joins_every_owned_basic_set_inventory_scope() -> None:
    rows = inventory(ROOT)
    catalog = next(
        family
        for family in matrix()["families"]
        if family["capability_id"] == "gurps.equipment.catalog"
    )
    selectors = {value.removeprefix("scope:") for value in catalog["owner_inventory_rows"][1:]}
    assert selectors == set(EXPECTED_SCOPE_COUNTS)
    for scope, expected_count in EXPECTED_SCOPE_COUNTS.items():
        owned = [
            row
            for row in rows
            if row.scope == scope and "gurps-basic-set-4e-2004" in row.required_profiles
        ]
        assert len(owned) == expected_count, scope
        assert all(row.source_review == "reviewed" and not row.gaps for row in owned), scope
        assert all(
            row.implementation in READY_IMPLEMENTATIONS
            or row.obligation in {"construction-catalog", "reference-only"}
            for row in owned
        ), scope


def test_exact_issue_728_set_is_verified_and_owned() -> None:
    for capability_id in EXPECTED:
        capability = CAPABILITIES[capability_id]
        assert capability.status is CoverageStatus.VERIFIED
        assert capability.owner_issue == 728
