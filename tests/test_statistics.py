"""GURPS attributes and secondary characteristics: profile selection and boundaries.

Expected values come from tests/fixtures/gurps/conformance.json or are stated
inline from the frozen source; none are read back from the implementation.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_compiler import compiler as prototype_compiler
from test_compiler import draft as prototype_draft
from test_compiler import trait

from wayfarer.character import statistics
from wayfarer.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Purchase,
    ValidatedBuild,
    pool_limits,
)
from wayfarer.character.statistics import (
    Advisory,
    Attribute,
    Encumbrance,
    PrimaryAttributes,
    RuntimePool,
    Secondary,
    SecondaryLevels,
    StatisticsError,
    carry_over,
    compile_statistics,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.advancement import _refreshed
from wayfarer.rules.catalog import (
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.rules.conformance import require_capabilities
from wayfarer.rules.effects import Effect, Operation
from wayfarer.simulation.resources import Pool

FIXTURE = Path(__file__).parent / "fixtures/gurps/conformance.json"
LITE = "gurps-lite-4e-2004"
BASIC = "gurps-basic-set-4e-2004"
CAPABILITIES = {
    "gurps.character.primary_attributes",
    "gurps.character.secondary_characteristics",
}
# Build revisions of prototype drafts recorded before this change landed. Saved
# campaigns pin these through approvals, so they must not move.
PROTOTYPE_REVISIONS = {
    "st11": "d0e9efbc87cbf9cd2905303c292fd1515dc1e3e0591efcb1f142228fbc1e3d62",
    "stealth": "bf09d2cfd4a377f2e6a656d3be81e35e6e787bc158490f0b91593e118c44355e",
}


def cases(operation: str | None = None) -> list[dict[str, object]]:
    data = json.loads(FIXTURE.read_text())
    return [
        case
        for case in data["cases"]
        if case["capability_id"] in CAPABILITIES
        and (operation is None or case.get("operation") == operation)
    ]


def inputs(case: dict[str, object]) -> dict[str, object]:
    value = case["input"]
    assert isinstance(value, dict)
    return value


def expected(case: dict[str, object]) -> dict[str, object]:
    value = case["expected"]
    assert isinstance(value, dict)
    return value


def profile_of(case: dict[str, object]) -> str:
    return str(case["profile"])


def test_every_statistics_fixture_declares_an_executable_operation() -> None:
    assert cases()
    assert all(case.get("operation") for case in cases())


@pytest.mark.parametrize("case", cases("attribute_cost"), ids=lambda c: str(c["id"]))
def test_attribute_costs(case: dict[str, object]) -> None:
    given_ = inputs(case)
    attribute = Attribute(str(given_["attribute"]).lower())
    assert given_["from"] == 10
    level = given_["to"]
    assert isinstance(level, int)
    assert (
        statistics.attribute_cost(profile_of(case), attribute, level)
        == expected(case)["point_cost"]
    )


@pytest.mark.parametrize("case", cases("basic_speed"), ids=lambda c: str(c["id"]))
def test_basic_speed_keeps_fractions(case: dict[str, object]) -> None:
    given_ = inputs(case)
    dx, ht = given_["DX"], given_["HT"]
    assert isinstance(dx, int) and isinstance(ht, int)
    result = compile_statistics(profile_of(case), PrimaryAttributes(10, dx, 10, ht))
    assert result.basic_speed == Decimal(str(expected(case)["basic_speed"]))
    assert str(result.basic_speed) == str(expected(case)["basic_speed"])


@pytest.mark.parametrize("case", cases("basic_move_from_speed"), ids=lambda c: str(c["id"]))
def test_basic_move_drops_fractions(case: dict[str, object]) -> None:
    quarters = Decimal(str(inputs(case)["basic_speed"])) * 4
    assert quarters == quarters.to_integral_value()
    result = compile_statistics(
        profile_of(case),
        PrimaryAttributes(10, 10, 10, 10),
        SecondaryLevels(basic_speed=int(quarters)),
    )
    assert result.basic_move == expected(case)["basic_move"]
    assert expected(case)["unit"] == "yard/second"


def _purchased(value: object) -> SecondaryLevels:
    assert isinstance(value, dict)
    return SecondaryLevels(
        hp=value.get("hp"),
        will=value.get("will"),
        per=value.get("per"),
        fp=value.get("fp"),
        basic_speed=value.get("basic_speed_quarters"),
        basic_move=value.get("basic_move"),
    )


@pytest.mark.parametrize("case", cases("secondary"), ids=lambda c: str(c["id"]))
def test_purchased_secondary_characteristics(case: dict[str, object]) -> None:
    given_ = inputs(case)
    attributes = PrimaryAttributes(*(int(str(given_[name])) for name in ("ST", "DX", "IQ", "HT")))
    result = compile_statistics(
        profile_of(case),
        attributes,
        _purchased(given_["purchased"]),
        revision=int(str(case.get("statistics_revision", 1))),
    )
    for key, value in expected(case).items():
        if key == "point_cost":
            assert sum(result.costs.secondaries.values()) == value, key
        elif key == "basic_speed":
            assert str(result.basic_speed) == value
        elif key == "advisories":
            assert [advisory.value for advisory in result.advisories] == value
        else:
            assert getattr(result, key) == value, key
    assert all(result.costs.attributes[a] == 0 for a in Attribute if attributes.level(a) == 10)


@pytest.mark.parametrize("case", cases("basic_lift"), ids=lambda c: str(c["id"]))
def test_basic_lift_rounding_point(case: dict[str, object]) -> None:
    st_ = inputs(case)["ST"]
    assert isinstance(st_, int)
    result = statistics.basic_lift(profile_of(case), st_)
    assert str(result) == expected(case)["basic_lift"]
    assert expected(case)["unit"] == "pound"
    assert (result == result.to_integral_value()) == (case["rounding"] == "nearest")


@pytest.mark.parametrize("case", cases("encumbrance"), ids=lambda c: str(c["id"]))
def test_encumbrance_bands(case: dict[str, object]) -> None:
    given_ = inputs(case)
    carried = given_["carried"]
    assert isinstance(carried, int)
    band = statistics.encumbrance(profile_of(case), Decimal(str(given_["basic_lift"])), carried)
    wanted = expected(case)["encumbrance"]
    if wanted is None:
        assert band is None
    else:
        assert band is not None and band.name.lower().replace("_", "-") == wanted


@pytest.mark.parametrize("case", cases("encumbered_move"), ids=lambda c: str(c["id"]))
def test_encumbered_move_and_dodge(case: dict[str, object]) -> None:
    given_ = inputs(case)
    band = Encumbrance[str(given_["encumbrance"]).upper().replace("-", "_")]
    move, dodge = given_["basic_move"], given_["dodge"]
    assert isinstance(move, int) and isinstance(dodge, int)
    assert statistics.encumbered_move(profile_of(case), move, band) == expected(case)["move"]
    assert statistics.encumbered_dodge(dodge, band) == expected(case)["dodge"]


@pytest.mark.parametrize("case", cases("damage"), ids=lambda c: str(c["id"]))
def test_damage_table_rows(case: dict[str, object]) -> None:
    st_ = inputs(case)["ST"]
    assert isinstance(st_, int)
    thrust, swing = statistics.damage(
        profile_of(case), st_, revision=int(str(case.get("statistics_revision", 1)))
    )
    assert (str(thrust), str(swing)) == (expected(case)["thrust"], expected(case)["swing"])


def test_damage_lookup_fails_closed_outside_the_profile_table() -> None:
    with pytest.raises(StatisticsError, match="no damage row") as lite:
        statistics.damage(LITE, 21)
