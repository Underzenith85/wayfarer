"""Basic Set Size Modifier pricing, integration, and fail-closed boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from test_statistics import BASIC, LITE, gurps_draft, profile_compiler, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild, pool_limits
from wayfarer.character.size_modifier import SizeCostTarget, SizeModifierError, cost
from wayfarer.character.statistics import Attribute
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.rules.conformance import CoverageStatus, capability
from wayfarer.rules.gurps_characters import (
    SIZE_MODIFIER_CAPABILITY_ID,
    SIZE_MODIFIER_DEFINITION_ID,
    size_modifier_definition,
)
from wayfarer.rules.profiles import DEFAULT_REGISTRY, GURPS_SIZE_PROFILE
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.resources import Pool

FIXTURE = Path("tests/fixtures/gurps/size_modifier_costs.json")


def fixture() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text())
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def engine() -> CharacterCompiler:
    package = profile_package(BASIC, size_modifier_definition())
    return profile_compiler(BASIC, package=package)


def size(amount: int, *, trait: TraitOptions | None = None) -> Purchase:
    return Purchase(definition_id=SIZE_MODIFIER_DEFINITION_ID, amount=amount, trait=trait)


def entries(build: ValidatedBuild) -> dict[str, int]:
    return {entry.definition_id: entry.cost for entry in build.purchases}


def test_independent_fixture_is_executable_and_source_pinned() -> None:
    data = fixture()
    assert data["capability_id"] == SIZE_MODIFIER_CAPABILITY_ID
    assert data["source_id"] == "sjg:basic-set-characters-4e-2004"
    assert data["references"] == ["B9", "B15", "B16"]
    cases = data["cases"]
    assert isinstance(cases, list) and cases
    for value in cases:
        assert isinstance(value, dict)
        case = cast(dict[str, object], value)
        given_value, expected_value = case["input"], case["expected"]
        assert isinstance(given_value, dict) and isinstance(expected_value, dict)
        given = cast(dict[str, object], given_value)
        expected = cast(dict[str, object], expected_value)
        target = given["target"]
        base_cost, size_modifier = given["base_cost"], given["size_modifier"]
        assert target in ("attribute:st", "secondary:hp")
        assert isinstance(base_cost, int) and not isinstance(base_cost, bool)
        assert isinstance(size_modifier, int) and not isinstance(size_modifier, bool)
        result = cost(BASIC, cast(SizeCostTarget, target), base_cost, size_modifier)
        assert result.adjusted_cost == expected["adjusted_cost"], case["id"]
        assert result.discount_percent == expected["discount_percent"], case["id"]
        assert result.source_id == data["source_id"]
        assert result.capability_id == SIZE_MODIFIER_CAPABILITY_ID


def test_size_modifier_capability_is_basic_only_and_verified() -> None:
    declared = capability(SIZE_MODIFIER_CAPABILITY_ID)
    assert declared.status is CoverageStatus.VERIFIED
    assert declared.owner_issue == 192
    assert declared.basic_required and not declared.lite_required
    with pytest.raises(SizeModifierError) as exc:
        cost(LITE, "attribute:st", 10, 1)
    assert exc.value.code == "size_modifier.profile"


def test_compiler_discounts_only_positive_st_and_hp_costs() -> None:
    result = engine().compile(
        gurps_draft(
            size(3),
            Purchase(definition_id="secondary:hp", amount=13),
            st_level=12,
        )
    )
    assert result.build is not None, result.diagnostics
    by_id = entries(result.build)
    assert by_id["attribute:st"] == 14
    assert by_id["secondary:hp"] == 2
    assert by_id[SIZE_MODIFIER_DEFINITION_ID] == 0
    assert result.build.statistics is not None
    assert result.build.statistics.costs.attributes[Attribute.ST] == 14
    assert [p.target for p in result.build.cost_provenance] == ["attribute:st", "secondary:hp"]
    assert [p.discount_percent for p in result.build.cost_provenance] == [30, 30]

    reduced = engine().compile(
        gurps_draft(
            size(4),
            Purchase(definition_id="secondary:hp", amount=8),
            st_level=9,
        )
    )
    assert reduced.build is not None, reduced.diagnostics
    reduced_costs = entries(reduced.build)
    assert reduced_costs["attribute:st"] == -10
    assert reduced_costs["secondary:hp"] == -2
    assert [p.discount_percent for p in reduced.build.cost_provenance] == [0, 0]


def test_rounding_can_keep_total_same_but_context_still_changes_revision() -> None:
    compiler = engine()
    low = compiler.compile(
        gurps_draft(size(1), Purchase(definition_id="secondary:hp", amount=11))
    ).build
    high = compiler.compile(
        gurps_draft(size(3), Purchase(definition_id="secondary:hp", amount=11))
    ).build
    assert low is not None and high is not None
    assert low.spent == high.spent == 2
    assert entries(low)["secondary:hp"] == entries(high)["secondary:hp"] == 2
    assert low.revision != high.revision
    assert low.cost_provenance != high.cost_provenance


def test_forged_or_unpinned_size_context_fails_closed() -> None:
    old = profile_compiler(BASIC)
    verdict = old.compile(gurps_draft(size(3)))
    assert verdict.build is None
    assert "definition.unknown" in {diagnostic.code for diagnostic in verdict.diagnostics}

    forged = engine().compile(
        gurps_draft(
            size(3, trait=TraitOptions(modifiers=("invented-discount",))),
            st_level=12,
        )
    )
    assert forged.build is None
    assert "trait.invalid" in {diagnostic.code for diagnostic in forged.diagnostics}


def test_repricing_does_not_change_hp_pool_maximum_or_heal_damage() -> None:
    compiler = engine()
    plain = compiler.compile(
        gurps_draft(Purchase(definition_id="secondary:hp", amount=13), st_level=12)
    ).build
    sized = compiler.compile(
        gurps_draft(size(3), Purchase(definition_id="secondary:hp", amount=13), st_level=12)
    ).build
    assert plain is not None and sized is not None
    assert pool_limits(plain) == pool_limits(sized) == {"hp": 13, "fp": 10}
    wounded = Pool(id="hp:a", current=7, maximum=13)
    assert _refreshed(wounded, pool_limits(sized)["hp"], sized) == wounded


def test_new_profile_pin_carries_context_without_mutating_historic_pins() -> None:
    newest = DEFAULT_REGISTRY.get("profile:gurps-basic-set-4e-2004", 5)
    historic = DEFAULT_REGISTRY.get("profile:gurps-basic-set-4e-2004", 4)
    assert newest is GURPS_SIZE_PROFILE
    assert newest.rules.packages[0].version == "0.5.0"
    assert historic.rules.packages[0].version == "0.4.0"
    assert SIZE_MODIFIER_DEFINITION_ID in {d.id for d in newest.packages[0].definitions}
    assert SIZE_MODIFIER_DEFINITION_ID not in {d.id for d in historic.packages[0].definitions}
