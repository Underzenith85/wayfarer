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

FIXTURE = Path("tests/fixtures/gurps/conformance.json")
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
    result = compile_statistics(profile_of(case), attributes, _purchased(given_["purchased"]))
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
    thrust, swing = statistics.damage(profile_of(case), st_)
    assert (str(thrust), str(swing)) == (expected(case)["thrust"], expected(case)["swing"])


def test_damage_lookup_fails_closed_outside_the_profile_table() -> None:
    with pytest.raises(StatisticsError, match="no damage row") as lite:
        statistics.damage(LITE, 21)
    assert lite.value.code == "damage.unsupported_st"
    assert statistics.damage(BASIC, 27) == statistics.damage(BASIC, 28)
    for unlisted in (41, 44, 99, 101, 0):
        with pytest.raises(StatisticsError, match="no damage row"):
            statistics.damage(BASIC, unlisted)


def test_profile_selection_is_exact_and_verified() -> None:
    require_capabilities(LITE, statistics.CAPABILITY_IDS)
    require_capabilities(BASIC, statistics.CAPABILITY_IDS)
    for bad in ("package:wayfarer-lite", "GURPS-LITE-4E-2004", "gurps-lite", ""):
        with pytest.raises(ValidationError, match="Unknown rules profile"):
            statistics.rules(bad)
    assert statistics.rules(LITE).damage_table_maximum_st == 20
    assert statistics.rules(BASIC).damage_table_maximum_st == 100
    assert statistics.source(LITE).id == "sjg:gurps-lite-4e-2004"
    assert statistics.source(LITE).rights == "user-supplied-reference"


def test_hard_limits_and_type_boundaries() -> None:
    with pytest.raises(StatisticsError, match="below the minimum") as low:
        compile_statistics(LITE, PrimaryAttributes(0, 10, 10, 10))
    assert low.value.code == "attribute.minimum"
    with pytest.raises(StatisticsError, match="at least one"):
        compile_statistics(LITE, PrimaryAttributes(10, 10, 10, 10), SecondaryLevels(hp=0))
    with pytest.raises(StatisticsError, match="cannot be negative"):
        compile_statistics(LITE, PrimaryAttributes(10, 10, 10, 10), SecondaryLevels(basic_move=-1))
    with pytest.raises(StatisticsError, match="integers"):
        compile_statistics(LITE, PrimaryAttributes(10, True, 10, 10))
    with pytest.raises(StatisticsError, match="negative"):
        statistics.encumbrance(LITE, Decimal(20), -1)
    minimal = compile_statistics(LITE, PrimaryAttributes(1, 1, 1, 1))
    assert minimal.basic_speed == Decimal("0.5") and minimal.basic_move == 0
    assert statistics.encumbered_move(LITE, 0, Encumbrance.NONE) == 0
    with pytest.raises(FrozenInstanceError):
        field = "hp"
        setattr(minimal, field, 99)


@given(st.integers(min_value=1, max_value=20), st.integers(min_value=1, max_value=20))
def test_basic_speed_and_move_invariants(dx: int, ht: int) -> None:
    result = compile_statistics(LITE, PrimaryAttributes(10, dx, 10, ht))
    assert result.basic_speed * 4 == dx + ht
    assert result.basic_move <= result.basic_speed < result.basic_move + 1
    assert result.dodge == result.basic_move + 3
    assert sum(result.costs.secondaries.values()) == 0


@given(st.integers(min_value=1, max_value=100))
def test_basic_lift_rounds_only_from_ten_pounds(st_: int) -> None:
    lift = statistics.basic_lift(BASIC, st_)
    exact = Decimal(st_ * st_) / 5
    if exact >= 10:
        assert lift == lift.to_integral_value() and abs(lift - exact) < Decimal("0.5")
    else:
        assert lift == exact


@given(
    st.integers(min_value=1, max_value=30),
    st.sampled_from(list(Encumbrance)),
)
def test_encumbered_move_never_exceeds_basic_move_or_drops_below_one(
    move: int, band: Encumbrance
) -> None:
    reduced = statistics.encumbered_move(LITE, move, band)
    assert 1 <= reduced <= move
    if band is Encumbrance.NONE:
        assert reduced == move


@given(
    st.integers(min_value=0, max_value=40),
    st.integers(min_value=0, max_value=40),
    st.integers(min_value=0, max_value=60),
)
def test_carry_over_preserves_the_deficit_and_never_heals(
    current: int, maximum: int, new_maximum: int
) -> None:
    if current > maximum:
        with pytest.raises(StatisticsError, match="outside its own limits"):
            carry_over(RuntimePool(current, maximum), new_maximum)
        return
    result = carry_over(RuntimePool(current, maximum), new_maximum)
    deficit = maximum - current
    assert result.maximum == new_maximum
    assert 0 <= result.current <= result.maximum
    assert result.maximum - result.current == min(deficit, new_maximum)
    assert result.current - current <= max(new_maximum - maximum, 0)
    assert carry_over(None, new_maximum) == RuntimePool(new_maximum, new_maximum)


def profile_package(profile_id: str, *extra: RuleDefinition) -> RulesPackage:
    return RulesPackage(
        id=f"package:{profile_id}-characters",
        version="1.0.0",
        edition="gurps-4e",
        sources=(statistics.source(profile_id), PROTOTYPE_SOURCE),
        definitions=statistics.definitions(profile_id) + extra,
    )


def profile_compiler(
    profile_id: str = LITE,
    *extra: RuleDefinition,
    effects: tuple[tuple[str, Effect], ...] = (),
    package: RulesPackage | None = None,
    statistics_profile: str | None = "same",
) -> CharacterCompiler:
    package = package or profile_package(profile_id, *extra)
    policy = CampaignPolicy(
        id="policy:gurps-test",
        version=1,
        point_budget=150,
        disadvantage_limit=200,
        attribute_ceiling=20,
        skill_ceiling=20,
        permitted_sources=frozenset(source.id for source in package.sources),
    )
    rules = CampaignRules(
        edition=package.edition,
        packages=(PackagePin(package.id, package.version, package.digest),),
        policy_id=policy.id,
        policy_version=policy.version,
    )
    return CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        policy,
        effects,
        statistics_profile=profile_id if statistics_profile == "same" else statistics_profile,
    )


def gurps_draft(*purchases: Purchase, st_level: int = 10) -> CharacterDraft:
    return CharacterDraft(
        name="Mira",
        purchases=tuple(
            Purchase(definition_id=f"attribute:{key}", amount=st_level if key == "st" else 10)
            for key in ("st", "dx", "iq", "ht")
        )
        + purchases,
    )


def test_prototype_builds_are_unchanged_and_reject_secondary_purchases() -> None:
    engine = prototype_compiler()
    build = engine.compile(prototype_draft(st_level=11)).build
    assert build is not None and build.statistics is None
    assert build.revision == PROTOTYPE_REVISIONS["st11"]
    skilled = engine.compile(
        prototype_draft(Purchase(definition_id="skill:stealth", amount=4))
    ).build
    assert skilled is not None and skilled.revision == PROTOTYPE_REVISIONS["stealth"]
    assert pool_limits(build) == {"hp": 11, "fp": 10}
    result = engine.compile(prototype_draft(Purchase(definition_id="secondary:hp", amount=12)))
    assert {d.code for d in result.diagnostics} == {"definition.unknown"}
    smuggled = replace(
        PROTOTYPE_PACKAGE,
        definitions=PROTOTYPE_PACKAGE.definitions
        + (
            RuleDefinition(
                "secondary:hp",
                DefinitionKind.SECONDARY,
                "HP",
                PROTOTYPE_SOURCE.id,
                2,
                ImplementationStatus.IMPLEMENTED,
            ),
        ),
    )
    with pytest.raises(ValidationError, match="require a selected rules profile"):
        profile_compiler(LITE, package=smuggled, statistics_profile=None)
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        profile_compiler(LITE, statistics_profile="package:wayfarer-lite")
    with pytest.raises(ValidationError, match="do not carry profile statistics"):
        profile_compiler(LITE, package=PROTOTYPE_PACKAGE)


def test_profile_compiler_projects_purchased_values_into_the_build() -> None:
    engine = profile_compiler()
    plain = engine.compile(gurps_draft()).build
    assert plain is not None and plain.statistics is not None
    assert plain.spent == 0 and plain.statistics.hp == 10 and plain.statistics.dodge == 8
    build = engine.compile(
        gurps_draft(
            Purchase(definition_id="secondary:hp", amount=13),
            Purchase(definition_id="secondary:basic-speed", amount=24),
            Purchase(definition_id="secondary:basic-move", amount=7),
            Purchase(definition_id="secondary:will", amount=9),
            st_level=12,
        )
    ).build
    assert build is not None and build.statistics is not None
    assert build.revision != plain.revision
    projection = build.statistics
    assert (projection.st, projection.hp, projection.will, projection.per) == (12, 13, 9, 10)
    assert projection.basic_speed == Decimal("6") and projection.basic_move == 7
    assert projection.dodge == 9 and projection.basic_lift == Decimal(29)
    assert (str(projection.thrust), str(projection.swing)) == ("1d-1", "1d+2")
    assert build.spent == 20 + 2 + 20 + 5 - 5 and build.disadvantages == 5
    values = {v.target: v.value for v in build.sheet.values}
    assert values["secondary:hp"] == 13 and values["secondary:basic-speed"] == Decimal("6")
    assert values["secondary:basic-lift"] == 29 and values["attribute:st"] == 12
    assert {e.definition_id: e.cost for e in build.purchases}["secondary:will"] == -5
    _, runtime = engine.activate(
        gurps_draft(Purchase(definition_id="secondary:hp", amount=13), st_level=12),
        authorize=lambda _: None,
    )
    assert (runtime.hp, runtime.fp) == (13, 10)
    assert engine.compile(gurps_draft(st_level=1)).legal
    assert "attribute.range" in {
        d.code for d in engine.compile(gurps_draft(st_level=21)).diagnostics
    }


def test_profile_compiler_reports_statistics_failures_and_duplicates() -> None:
    engine = profile_compiler(BASIC)
    duplicated = engine.compile(
        gurps_draft(
            Purchase(definition_id="secondary:hp", amount=12),
            Purchase(definition_id="secondary:hp", amount=13),
        )
    )
    assert {d.code for d in duplicated.diagnostics} == {"purchase.duplicate"}
    missing = engine.compile(
        CharacterDraft(name="Mira", purchases=(Purchase(definition_id="secondary:hp", amount=12),))
    )
    assert "attribute.required" in {d.code for d in missing.diagnostics}
    assert missing.build is None and missing.spent == 0
    over_table = engine.compile(gurps_draft(st_level=17))
    assert over_table.legal
    lite = profile_compiler(LITE)
    assert "attribute.range" in {d.code for d in lite.compile(gurps_draft(st_level=21)).diagnostics}
    ceiling = CampaignPolicy(
        id="policy:gurps-test",
        version=1,
        point_budget=1000,
        disadvantage_limit=50,
        attribute_ceiling=40,
        skill_ceiling=20,
        permitted_sources=frozenset({statistics.source(LITE).id, PROTOTYPE_SOURCE.id}),
    )
    package = profile_package(LITE)
    wide = CharacterCompiler(
        RulesCatalog((package,)),
        CampaignRules(
            package.edition,
            (PackagePin(package.id, package.version, package.digest),),
            ceiling.id,
            1,
        ),
        ceiling,
        statistics_profile=LITE,
    )
    codes = {d.code for d in wide.compile(gurps_draft(st_level=21)).diagnostics}
    assert codes == {"damage.unsupported_st"}


def test_effects_layer_on_secondary_targets_and_feed_runtime_pools() -> None:
    bonus = Effect("tough-hp", "secondary:hp", Operation.ADD, Decimal(2), "tough", "1.0.0")
    tough = replace(trait("tough"), source_id=PROTOTYPE_SOURCE.id)
    engine = profile_compiler(LITE, tough, effects=(("tough", bonus),))
    build, runtime = engine.activate(
        gurps_draft(
            Purchase(definition_id="tough"), Purchase(definition_id="secondary:hp", amount=12)
        ),
        authorize=lambda _: None,
    )
    assert build.statistics is not None and build.statistics.hp == 12
    hp = next(v for v in build.sheet.values if v.target == "secondary:hp")
    assert hp.value == 14 and hp.explanations[0].source_id == "tough"
    assert pool_limits(build) == {"hp": 14, "fp": 10} and runtime.hp == 14


def test_recompilation_moves_ceilings_without_healing() -> None:
    engine = profile_compiler()
    wounded = Pool(id="hp:a", current=6, maximum=10)
    same = engine.compile(gurps_draft()).build
    assert same is not None
    assert _refreshed(wounded, pool_limits(same)["hp"], same) == wounded
    raised = engine.compile(gurps_draft(Purchase(definition_id="secondary:hp", amount=13))).build
    assert raised is not None
    assert _refreshed(wounded, pool_limits(raised)["hp"], raised) == Pool(
        id="hp:a", current=9, maximum=13
    )
    lowered = engine.compile(gurps_draft(Purchase(definition_id="secondary:hp", amount=3))).build
    assert lowered is not None
    assert _refreshed(wounded, pool_limits(lowered)["hp"], lowered) == Pool(
        id="hp:a", current=0, maximum=3
    )
    prototype = prototype_compiler().compile(prototype_draft(st_level=13)).build
    assert prototype is not None
    assert _refreshed(wounded, pool_limits(prototype)["hp"], prototype) == Pool(
        id="hp:a", current=6, maximum=13
    )


def test_pool_limits_reject_non_integral_values() -> None:
    engine = profile_compiler(
        LITE,
        replace(trait("half"), source_id=PROTOTYPE_SOURCE.id),
        effects=(
            (
                "half",
                Effect("half-hp", "secondary:hp", Operation.ADD, Decimal("0.5"), "half", "1.0.0"),
            ),
        ),
    )
    build = engine.compile(gurps_draft(Purchase(definition_id="half"))).build
    assert isinstance(build, ValidatedBuild)
    with pytest.raises(ValidationError, match="not a whole number"):
        pool_limits(build)


def test_definitions_are_catalog_ready_and_named() -> None:
    names = {d.id: d.name for d in statistics.definitions(BASIC)}
    assert names["secondary:basic-speed"] == "Basic Speed" and names["attribute:st"] == "ST"
    assert all(
        d.kind is DefinitionKind.SECONDARY
        for d in statistics.definitions(LITE)
        if "secondary" in d.id
    )
    assert {s.value for s in Secondary} == {
        key.split(":", 1)[1] for key in names if key.startswith("secondary:")
    }
    assert Advisory.HP_BEYOND_GUIDELINE.value == "hp.beyond-30-percent-of-st"
