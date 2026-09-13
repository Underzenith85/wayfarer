"""Basic Set certification must fail closed until every evidence dimension passes."""

from pathlib import Path

import pytest

from wayfarer.certification.basic_set_certification import PROFILE_ID, evaluate, require_certified
from wayfarer.engine.rules.conformance import PROFILES
from wayfarer.errors import ValidationError

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
    assert result.excluded_content == ("gurps.content.infinite-worlds",)


def test_basic_set_gate_exposes_capability_source_and_inventory_blockers() -> None:
    result = evaluate(ROOT)
    assert result.certified is False
    kinds = {blocker.kind for blocker in result.blockers}
    assert {"ledger", "capability", "inventory"} <= kinds
    capabilities = [blocker for blocker in result.blockers if blocker.kind == "capability"]
    assert capabilities
    assert all(blocker.owner_issue is not None for blocker in capabilities)
    # #192 verified Size Modifier costs, so that capability no longer blocks certification.
    assert not any(
        blocker.identifier == "gurps.character.size_modifier_costs" for blocker in capabilities
    )
    assert not any(
        blocker.kind == "source" and "lite" in blocker.identifier for blocker in result.blockers
    )
    assert not any(
        "source_review=pending" in blocker.detail
        for blocker in result.blockers
        if blocker.kind == "inventory"
    )


def test_inventory_cannot_outrun_bound_source_ledger_implementation() -> None:
    result = evaluate(ROOT)
    inventory_blockers = {
        blocker.identifier: blocker for blocker in result.blockers if blocker.kind == "inventory"
    }

    # The runtime hook exists, but the exhaustive source row still records only
    # partial rule coverage. The inventory row must therefore remain blocked.
    combat_reflexes = inventory_blockers["trait:combat-reflexes"]
    assert "implementation=implemented" in combat_reflexes.detail
    assert "source_review=reviewed" in combat_reflexes.detail
    assert "source_ledger_implementation=partial" in combat_reflexes.detail
    assert combat_reflexes.owner_issue == 496

    # A row whose runtime and bound source evidence are both verified remains
    # eligible and must not acquire a duplicate inventory blocker.
    assert "development:adventure" not in inventory_blockers


def test_inventory_blocker_reports_itemized_creature_gaps() -> None:
    result = evaluate(ROOT)
    blockers = {
        blocker.identifier: blocker for blocker in result.blockers if blocker.kind == "inventory"
    }
    cat = blockers["creature:house-cat"]
    assert cat.owner_issue == 521
    assert "implementation=partial" in cat.detail
    assert "gaps=catfall,combat-reflexes,domestic-animal" in cat.detail
    assert "swarm:bees" in blockers
    assert "swarm:bats" not in blockers
    assert "swarm:rats" not in blockers


def test_basic_set_release_rejects_current_incomplete_evidence() -> None:
    with pytest.raises(ValidationError, match="Basic Set certification blocked"):
        require_certified(ROOT)


def test_report_serialization_keeps_blockers_machine_readable() -> None:
    payload = evaluate(ROOT).as_dict()
    assert payload["certified"] is False
    assert payload["excluded_content"] == ["gurps.content.infinite-worlds"]
    blockers = payload["blockers"]
    assert isinstance(blockers, list)
    assert blockers
    for blocker in blockers:
        assert isinstance(blocker, dict)
        assert {"kind", "identifier", "detail", "owner_issue"} <= set(blocker)
