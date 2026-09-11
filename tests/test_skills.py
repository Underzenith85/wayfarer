"""Independent numbers from Lite 13-14 / B170, B173, B229-232, frozen baseline.

Generic test-only definitions exercise mechanics, not claims of catalog coverage.
"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.character.skills import BASIC, LITE, SkillCompiler, SkillError, relative_level
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DEFAULT_CATALOG,
    DEFAULT_POLICY,
    DEFAULT_RULES,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.gurps_skills import definitions
from wayfarer.rules.profiles import (
    DEFAULT_REGISTRY,
    GURPS_BASIC_PROFILE,
    GURPS_BASIC_PROFILE_V2,
    GURPS_LITE_PROFILE,
    GURPS_LITE_PROFILE_V2,
    RegisteredProfile,
)
from wayfarer.rules.skill_types import (
    ControllingAttribute as A,
)
from wayfarer.rules.skill_types import (
    Difficulty as D,
)
from wayfarer.rules.skill_types import (
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
    Technique,
)
from wayfarer.simulation.events import action_result


def attrs(**overrides: int) -> dict[str, Decimal]:
    values = {a.value: Decimal(10) for a in A}
    values.update({key: Decimal(value) for key, value in overrides.items()})
    return values


def definition(key: str, spec: SkillSpec) -> RuleDefinition:
    return RuleDefinition(
        key,
        DefinitionKind.SKILL,
        key,
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=spec,
    )


def skill_engine(*rows: RuleDefinition, profile_id: str = BASIC) -> SkillCompiler:
    return SkillCompiler(profile_id, {d.id: d for d in rows or definitions(profile_id)})


def compiler(profile: RegisteredProfile = GURPS_BASIC_PROFILE) -> CharacterCompiler:
    return CharacterCompiler(
        profile.catalog,
        profile.rules,
        replace(profile.policy, point_budget=1000, skill_ceiling=50, attribute_ceiling=25),
        statistics_profile=profile.conformance_profile_id,
    )


def draft(**purchases: int) -> CharacterDraft:
    amounts = {
        "attribute:st": 10,
        "attribute:dx": 10,
        "attribute:iq": 10,
        "attribute:ht": 10,
        **purchases,
    }
    return CharacterDraft(
        name="Skills",
        purchases=tuple(Purchase(definition_id=k, amount=v) for k, v in amounts.items()),
    )


def sheet(engine: CharacterCompiler, **purchases: int) -> dict[str, Decimal]:
    result = engine.compile(draft(**purchases))
    assert result.build is not None, result.diagnostics
    return {v.target: v.value for v in result.build.sheet.values}


def cases() -> list[dict[str, object]]:
    data = json.loads(Path("tests/fixtures/gurps/conformance.json").read_text())
    return [case for case in data["cases"] if case.get("service") == "skills"]


@pytest.mark.parametrize("case", cases(), ids=lambda c: str(c["id"]))
def test_source_referenced_golden_cases(case: dict[str, object]) -> None:
    inputs = case["input"]
    expected = case["expected"]
    assert isinstance(inputs, dict) and isinstance(expected, dict)
    assert case["provenance"] and case["reference"]
    if inputs["operation"] == "progression":
        skill = definition("skill:test", SkillSpec(A.DX, D(inputs["difficulty"]), "B170; Lite 13"))
        result = skill_engine(skill, profile_id=str(case["profile"])).compile(
            {"skill:test": inputs["points"]}, attrs(**{"attribute:dx": inputs["attribute"]})
        )
        assert result[0].level == expected["level"]
    else:
        engine = skill_engine(profile_id=str(case["profile"]))
        if "error" in expected:
            with pytest.raises(SkillError) as caught:
                engine.compile(inputs["points"], attrs(**inputs.get("attributes", {})))
            assert caught.value.code == expected["error"]
        else:
            results = {
                s.target: s
                for s in engine.compile(inputs["points"], attrs(**inputs.get("attributes", {})))
            }
            result_ = results[inputs["target"]]
            assert result_.level == expected["level"]
            if "default_from" in expected:
                assert result_.default_from == expected["default_from"]
            if "credit" in expected:
                assert result_.default_credit == expected["credit"]


def test_default_chains_require_training_at_every_link() -> None:
    rows = (
        definition("a", SkillSpec(A.DX, D.AVERAGE, "B173", (SkillDefault(A.DX, -5),))),
        definition("b", SkillSpec(A.DX, D.AVERAGE, "B173", (SkillDefault("a", -2),))),
        definition("c", SkillSpec(A.DX, D.AVERAGE, "B173", (SkillDefault("b", -2),))),
    )
    engine = skill_engine(*rows)
    levels = {s.target: s.level for s in engine.compile({}, attrs())}
    assert levels == {"a": 5}  # No double defaults.
    levels = {s.target: s.level for s in engine.compile({"a": 20}, attrs())}
    assert levels == {"a": 15, "b": 13}
    levels = {s.target: s.level for s in engine.compile({"a": 20, "b": 4}, attrs())}
    assert levels == {"a": 15, "b": 14, "c": 12}


def test_prerequisite_cannot_be_met_by_a_default_or_insufficient_training() -> None:
    rows = (
        definition("a", SkillSpec(A.DX, D.EASY, "B169", (SkillDefault(A.DX, -4),))),
        definition(
            "b", SkillSpec(A.DX, D.HARD, "B169", prerequisites=(SkillPrerequisite("a", 12),))
        ),
    )
    engine = skill_engine(*rows)
    for points in ({"b": 1}, {"a": 1, "b": 1}):
        with pytest.raises(SkillError, match="prerequisite"):
            engine.compile(points, attrs())
    assert {s.target: s.level for s in engine.compile({"a": 4, "b": 1}, attrs())} == {
        "a": 12,
        "b": 8,
    }


@pytest.mark.parametrize("points", [0, -1, True, 1.5])
def test_point_inputs_are_strict(points: int) -> None:
    with pytest.raises(SkillError):
        relative_level(D.EASY, points)


@given(st.integers(1, 1000), st.sampled_from(list(D)))
def test_progression_never_decreases(points: int, difficulty: D) -> None:
    assert relative_level(difficulty, points + 1) >= relative_level(difficulty, points)
    if points >= 4:
        assert relative_level(difficulty, points + 4) == relative_level(difficulty, points) + 1


def test_definitions_reject_cycles_missing_references_and_unsupported_mechanics() -> None:
    a = definition("a", SkillSpec(A.DX, D.AVERAGE, "B173", (SkillDefault("b", -2),)))
    b = definition("b", SkillSpec(A.DX, D.AVERAGE, "B173", (SkillDefault("a", -2),)))
    with pytest.raises(SkillError, match="Cyclic"):
        skill_engine(a, b)
    with pytest.raises(SkillError, match="reference"):
        skill_engine(a)
    with pytest.raises(SkillError, match="outside Lite"):
        skill_engine(definition("vh", SkillSpec(A.IQ, D.VERY_HARD, "B170")), profile_id=LITE)
    with pytest.raises(SkillError, match="technique"):
        skill_engine(definition("a", SkillSpec(A.DX, D.EASY, "B229", technique=Technique("a", -2))))
    with pytest.raises(SkillError, match="specialty"):
        skill_engine(
            definition("a", SkillSpec(A.IQ, D.AVERAGE, "B169", specialty=Specialty("", "")))
        )


def test_compiler_uses_effective_attributes_points_and_distinct_specialties() -> None:
    engine = compiler()
    values = sheet(
        engine,
        **{
            "secondary:per": 14,
            "skill:observation": 4,
            "skill:survival-woodlands": 1,
            "skill:stealth": 20,
        },
    )
    assert values["skill:observation"] == 15
    assert values["skill:survival-woodlands"] == 13
    assert values["skill:stealth"] == 15
    values = sheet(engine, **{"attribute:dx": 12, "skill:stealth": 20})
    assert values["skill:stealth"] == 17
    assert values["skill:observation"] == 5
    assert "skill:karate" not in values
    illegal = engine.compile(draft(**{"skill:survival": 1}))
    assert illegal.build is None and illegal.diagnostics[0].code == "definition.unknown"
    illegal = engine.compile(draft(**{"skill:kicking-karate": 3}))
    assert illegal.build is None and illegal.diagnostics[0].code == "skill.prerequisite"


def test_profile_versions_preserve_old_pins_and_metadata_changes_digest() -> None:
    for old in (GURPS_LITE_PROFILE_V2, GURPS_BASIC_PROFILE_V2):
        assert DEFAULT_REGISTRY.resolve(old.reference) == old
        assert not any(d.skill for p in old.packages for d in p.definitions)
    package = GURPS_BASIC_PROFILE.packages[0]
    skill = next(d for d in package.definitions if d.skill)
    assert skill.skill is not None
    changed = replace(skill, skill=replace(skill.skill, difficulty=D.HARD))
    altered = replace(
        package, definitions=tuple(changed if d.id == skill.id else d for d in package.definitions)
    )
    assert altered.digest != package.digest
    with pytest.raises(ValidationError, match="pin does not resolve"):
        RulesCatalog((altered,)).package(PackagePin(package.id, package.version, package.digest))
    with pytest.raises(ValidationError, match="require a selected rules profile"):
        # No implicit GURPS dispatch in the prototype compiler.
        CharacterCompiler(
            GURPS_LITE_PROFILE.catalog, GURPS_LITE_PROFILE.rules, GURPS_LITE_PROFILE.policy
        )


def test_prototype_curve_stays_separate() -> None:
    engine = CharacterCompiler(DEFAULT_CATALOG, DEFAULT_RULES, DEFAULT_POLICY)
    assert sheet(engine, **{"skill:stealth": 4})["skill:stealth"] == 11
    assert engine.compile(draft(**{"skill:stealth": 20})).build is None


def test_effects_propagate_to_defaults_and_techniques_without_double_application() -> None:
    from wayfarer.rules.effects import Effect, Operation

    profile = GURPS_BASIC_PROFILE
    engine = compiler()
    engine = CharacterCompiler(
        profile.catalog,
        profile.rules,
        engine.policy,
        effects=(
            (
                "skill:broadsword",
                Effect(
                    "training",
                    "skill:broadsword",
                    Operation.ADD,
                    Decimal(2),
                    "skill:broadsword",
                    "0.3.0",
                ),
            ),
        ),
        statistics_profile=BASIC,
    )
    values = sheet(engine, **{"skill:broadsword": 20})
    assert values["skill:broadsword"] == 17
    assert values["skill:shortsword"] == 15
    # No purchase, no default: a bonus cannot create trained Karate.
    engine = CharacterCompiler(
        profile.catalog,
        profile.rules,
        engine.policy,
        effects=(
            (
                "attribute:dx",
                Effect("bonus", "skill:karate", Operation.ADD, Decimal(2), "attribute:dx", "0.3.0"),
            ),
        ),
        statistics_profile=BASIC,
    )
    assert "skill:karate" not in sheet(engine)


def test_actual_action_accepts_unpurchased_default_and_uses_per() -> None:
    from wayfarer.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.simulation.action_engine import ActionEngine
    from wayfarer.simulation.actions import (
        ActionRules,
        ActorSetup,
        CheckRule,
        Inspect,
        PlayActor,
        PlayState,
    )
    from wayfarer.simulation.resources import Owner, Pool, ResourceEngine, ResourceState
    from wayfarer.world import Entity, EntityKind, Fact, World

    class Dice:
        def randbelow(self, exclusive_upper_bound: int, /) -> int:
            return 0

    world = World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Actor", location_id="room"),
            Entity("chest", EntityKind.OBJECT, "Chest", location_id="room"),
        ),
        facts=(Fact("clue", "chest", "contains", "letter"),),
    )

    character_compiler = compiler()
    profile = GURPS_BASIC_PROFILE
    reviewer = PowerReviewer(
        character_compiler,
        PowerPolicy(id="power", version=1, automatic_approval=True),
        frozenset({"gm"}),
    )
    resources = ResourceEngine(world, profile.catalog, profile.rules, character_compiler.policy, ())
    rule = CheckRule(
        id="inspect",
        action="inspect",
        target_id="chest",
        definition_id="skill:observation",
        package_id=profile.packages[0].id,
        package_version="0.3.0",
        duration=1,
        reveal_fact_ids=("clue",),
    )
    engine = ActionEngine(reviewer, resources, ActionRules(id="skills", version=1, checks=(rule,)))
    setup = ActorSetup(
        actor_id="a",
        proposal=CharacterProposal(draft=draft(**{"secondary:per": 14})),
        aware_of=("chest",),
    )
    approval = reviewer.approve(setup.proposal, campaign_id="c", actor_id="a", revision=0)
    state = PlayState(
        campaign_id="c",
        configuration_digest=engine.digest,
        world=world,
        resources=ResourceState(
            owners=(
                Owner(
                    actor_id="a",
                    capacity=100,
                    definitions=tuple(p.definition_id for p in setup.proposal.draft.purchases),
                ),
            ),
            pools=(
                Pool(id="hp:a", current=10, maximum=10),
                Pool(id="fp:a", current=10, maximum=10),
            ),
        ),
        actors=(PlayActor(**setup.model_dump(), approval=approval),),
        approvals=(approval,),
    )
    action = Inspect(id="inspect", actor_id="a", target_id="chest", expected_revision=0)
    assert engine.assess(state, action).status == "feasible"
    _, resolved_events = engine.resolve(state, action, rng=Dice())
    result = action_result(resolved_events)
    assert result.check is not None
    assert result.check.effective_target == 9


def test_policy_availability_blocks_defaults_as_well_as_purchases() -> None:
    rows = {d.id: d for d in definitions(BASIC)}
    allowed = frozenset(rows) - {"skill:broadsword", "skill:observation"}
    engine = SkillCompiler(BASIC, rows, allowed)
    levels = {s.target for s in engine.compile({}, attrs())}
    assert "skill:observation" not in levels
    assert "skill:broadsword" not in levels
    with pytest.raises(SkillError):
        engine.compile({"skill:broadsword": 1}, attrs())
