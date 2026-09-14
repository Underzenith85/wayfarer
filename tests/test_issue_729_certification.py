"""Certification evidence joins for the seven capability families in #729."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from wayfarer.certification.source_ledgers import load_source_ledgers
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.skills.mundane import inventory

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "src/wayfarer/certification/basic_set_audit/capability-evidence-729.json"
CAPABILITIES_729 = {
    "gurps.skills.arts_trades",
    "gurps.skills.knowledge_investigation",
    "gurps.skills.medicine_mental",
    "gurps.skills.physical_outdoors",
    "gurps.skills.technology_vehicles",
    "gurps.world.environmental_hazards",
    "gurps.world.physical_feats",
}
SKILL_OWNERS = {338: 62, 341: 27, 342: 22, 343: 70, 346: 83}


def evidence() -> dict[str, object]:
    return cast(dict[str, object], json.loads(MATRIX.read_text()))


def test_matrix_pins_selected_sources_and_exact_capability_set() -> None:
    data = evidence()
    assert data["schema_version"] == 1 and data["issue"] == 729
    assert data["baseline_id"] == "gurps-4e-characters-3p-2008+campaigns-4p-2008"
    assert data["profile"] == "gurps-basic-set-4e-2004"
    sources = cast(dict[str, dict[str, object]], data["sources"])
    assert sources["characters"]["sha256"] == (
        "872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e"
    )
    assert sources["campaigns"]["sha256"] == (
        "79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80"
    )
    entries = cast(list[dict[str, object]], data["capabilities"])
    assert {entry["capability_id"] for entry in entries} == CAPABILITIES_729
    assert all(entry["references"] and entry["variants"] for entry in entries)
    assert all(entry["execution_paths"] and entry["evidence"] for entry in entries)


def test_every_matrix_evidence_artifact_exists() -> None:
    entries = cast(list[dict[str, object]], evidence()["capabilities"])
    for entry in entries:
        for artifact in cast(list[str], entry["evidence"]):
            assert (ROOT / artifact).is_file(), (entry["capability_id"], artifact)


def test_all_owned_skill_rows_are_bound_to_executable_procedures() -> None:
    rows = inventory()
    for owner, expected_count in SKILL_OWNERS.items():
        owned = [row for row in rows if row.procedure_owner == owner]
        assert len(owned) == expected_count
        assert all(row.bound and not row.blockers for row in owned)
        assert all(row.implementation in {"implemented", "contextual"} for row in owned)


def test_world_source_rows_are_reviewed_and_executable() -> None:
    rows = load_source_ledgers(ROOT).rows
    for capability_id in {
        "gurps.world.environmental_hazards",
        "gurps.world.physical_feats",
    }:
        owned = [row for row in rows if row.capability_id == capability_id]
        assert owned
        assert all(row.source_review == "reviewed" for row in owned)
        assert all(
            row.disposition != "required" or row.implementation in {"implemented", "verified"}
            for row in owned
        )


def test_exact_issue_729_set_is_verified_and_owned() -> None:
    for capability_id in CAPABILITIES_729:
        capability = CAPABILITIES[capability_id]
        assert capability.status is CoverageStatus.VERIFIED
        assert capability.owner_issue == 729
