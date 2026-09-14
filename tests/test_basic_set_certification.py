"""Basic Set certification passes only when every evidence dimension is complete."""

from pathlib import Path

from wayfarer.certification.basic_set_certification import PROFILE_ID, evaluate, require_certified
from wayfarer.engine.rules.conformance import PROFILES

ROOT = Path(__file__).resolve().parents[1]


def test_basic_set_report_binds_exact_profile_and_source_baseline() -> None:
    result = evaluate(ROOT)
    assert result.profile_id == "profile:gurps-basic-set-4e-2004"
    assert result.profile_version >= 1
    assert len(result.profile_digest) == 64
    assert result.source_baseline == "gurps-4e-characters-3p-2008+campaigns-4p-2008"
    assert result.required_capabilities == len(PROFILES[PROFILE_ID].required_capabilities)
    assert 0 < result.verified_capabilities <= result.required_capabilities
    assert result.required_inventory_items > 0
    assert sum(result.inventory_obligation_rollups.values()) == result.required_inventory_items
    assert result.inventory_obligation_rollups["executable-mechanic"] > 0
    assert result.inventory_obligation_rollups["construction-catalog"] > 0
    assert result.inventory_obligation_rollups["reference-only"] > 0
    assert "unsupported-required" not in result.inventory_obligation_rollups
    assert result.excluded_content == ("gurps.content.infinite-worlds",)


def test_basic_set_gate_is_certified_without_blockers() -> None:
    result = evaluate(ROOT)
    assert result.certified is True
    assert result.verified_capabilities == result.required_capabilities
    assert result.blockers == ()


def test_inventory_uses_reconciled_bound_source_ledger_implementation() -> None:
    result = evaluate(ROOT)
    inventory_blockers = {
        blocker.identifier: blocker for blocker in result.blockers if blocker.kind == "inventory"
    }

    # Reviewed runtime bindings with independent test evidence are joined to
    # their source-ledger implementation and do not create duplicate blockers.
    assert "trait:combat-reflexes" not in inventory_blockers
    assert "supernatural/advantage:360-vision" not in inventory_blockers
    assert "development:adventure" not in inventory_blockers


def test_completed_creature_inventory_has_no_fallback_blockers() -> None:
    result = evaluate(ROOT)
    blockers = {
        blocker.identifier: blocker for blocker in result.blockers if blocker.kind == "inventory"
    }
    assert (
        not {
            "creature:house-cat",
            "creature:large-guard-dog",
            "creature:timber-wolf",
            "creature:cavalry-horse",
            "creature:draft-horse",
            "creature:basilisk",
            "creature:gryphon",
            "swarm:bees",
            "swarm:bats",
            "swarm:rats",
        }
        & blockers.keys()
    )


def test_certified_report_has_no_blocker_owners() -> None:
    assert evaluate(ROOT).blockers == ()


def test_basic_set_release_accepts_complete_evidence() -> None:
    result = require_certified(ROOT)
    assert result.certified is True
    assert result == evaluate(ROOT)


def test_report_serialization_keeps_blockers_machine_readable() -> None:
    payload = evaluate(ROOT).as_dict()
    assert payload["inventory_obligation_rollups"] == evaluate(ROOT).inventory_obligation_rollups
    assert payload["certified"] is True
    assert payload["excluded_content"] == ["gurps.content.infinite-worlds"]
    blockers = payload["blockers"]
    assert isinstance(blockers, list)
    assert blockers == []
