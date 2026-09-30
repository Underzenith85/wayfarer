"""Synthetic acquisition thresholds; no published spell metadata is asserted."""

from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest
from test_magic_catalog import draft
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.skills import DefaultContext, SkillCompiler, SkillError
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.rules.magic.gurps_magic import MAGERY, PROFILE
from wayfarer.engine.rules.profiles import GURPS_MAGIC_PROFILE
from wayfarer.engine.rules.types.skill import (
    ControllingAttribute,
    Difficulty,
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillPrerequisite,
    SkillSpec,
)

SOURCE = "source:synthetic-acquisition-test"
TARGET = "skill:synthetic-acquisition"
OWNER = "trait:synthetic-owner"
ATTRIBUTES = {attribute.value: Decimal(12) for attribute in ControllingAttribute}


def definition(
    requirement: SkillPrerequisite, alternatives: bool = False
) -> RuleDefinition:
    other = SkillPrerequisite("capability:synthetic", kind=PrerequisiteKind.CAPABILITY)
    return RuleDefinition(
        TARGET,
        DefinitionKind.SKILL,
        "Synthetic acquisition",
        SOURCE,
        1,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill",),
        skill=SkillSpec(
            ControllingAttribute.IQ,
            Difficulty.VERY_HARD,
            "synthetic:level-threshold",
            prerequisites=() if alternatives else (requirement,),
            prerequisite_groups=(PrerequisiteGroup((requirement, other)),)
            if alternatives
            else (),
        ),
    )


def engine(minimum: int = 3, alternatives: bool = False) -> SkillCompiler:
    entry = definition(
        SkillPrerequisite(OWNER, minimum, PrerequisiteKind.PURCHASED_DEFINITION),
        alternatives,
    )
    return SkillCompiler(PROFILE, {TARGET: entry})


@pytest.mark.parametrize("level", [1, 2])
@pytest.mark.parametrize("alternatives", [False, True])
def test_purchase_presence_cannot_satisfy_a_higher_level(level: int, alternatives: bool) -> None:
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        engine(alternatives=alternatives).compile(
            {TARGET: 4},
            ATTRIBUTES,
            default_context=DefaultContext(
                {},
                frozenset(),
                purchased_definition_ids=frozenset({OWNER}),
                purchased_definition_levels={OWNER: level},
            ),
        )


@pytest.mark.parametrize("level", [3, 4])
@pytest.mark.parametrize("alternatives", [False, True])
def test_threshold_and_higher_amount_compile_actual_skill_levels(
    level: int, alternatives: bool
) -> None:
    compiled = engine(alternatives=alternatives).compile(
        {TARGET: 4},
        ATTRIBUTES,
        default_context=DefaultContext(
            {},
            frozenset(),
            purchased_definition_ids=frozenset({OWNER}),
            purchased_definition_levels={OWNER: level},
        ),
    )
    assert [(value.target, value.level, value.points) for value in compiled] == [
        (TARGET, 11, 4)
    ]


def test_presence_only_is_compatible_only_with_minimum_one() -> None:
    context = DefaultContext({}, frozenset(), purchased_definition_ids=frozenset({OWNER}))
    assert engine(1).compile({TARGET: 1}, ATTRIBUTES, default_context=context)[0].level == 9
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        engine().compile({TARGET: 4}, ATTRIBUTES, default_context=context)


def test_another_alternative_retains_its_capability_semantics() -> None:
    context = DefaultContext({}, frozenset(), capabilities=frozenset({"capability:synthetic"}))
    assert engine(alternatives=True).compile(
        {TARGET: 4}, ATTRIBUTES, default_context=context
    )[0].level == 11


@pytest.mark.parametrize("level", [True, 0, -1, 1.5, "3"])
def test_invalid_purchase_levels_are_rejected(level: object) -> None:
    context = DefaultContext(
        {},
        frozenset(),
        purchased_definition_ids=frozenset({OWNER}),
        purchased_definition_levels={OWNER: cast(int, level)},
    )
    with pytest.raises(SkillError, match="Purchased levels"):
        engine().compile({TARGET: 4}, ATTRIBUTES, default_context=context)


def test_level_facts_require_purchase_presence() -> None:
    context = DefaultContext({}, frozenset(), purchased_definition_levels={OWNER: 3})
    with pytest.raises(SkillError, match="Purchased levels"):
        engine().compile({TARGET: 4}, ATTRIBUTES, default_context=context)


def character_engine() -> CharacterCompiler:
    profile = GURPS_MAGIC_PROFILE
    # This is a synthetic catalog requirement on an existing leveled trait.
    # It deliberately asserts no Major/Great Healing, Enchant or Plane Shift rules.
    entry = definition(
        SkillPrerequisite(MAGERY, 3, PrerequisiteKind.PURCHASED_DEFINITION)
    )
    package = RulesPackage(
        "package:synthetic-acquisition-test",
        "1.0.0",
        profile.rules.edition,
        (SourceReference(SOURCE, "Synthetic acquisition test", "original"),),
        (entry,),
    )
    return CharacterCompiler(
        RulesCatalog(profile.packages + (package,)),
        replace(
            profile.rules,
            packages=profile.rules.packages
            + (PackagePin(package.id, package.version, package.digest),),
        ),
        replace(
            profile.policy,
            permitted_sources=profile.policy.permitted_sources | {SOURCE},
            point_budget=500,
            skill_ceiling=30,
        ),
        statistics_profile=PROFILE,
    )


@pytest.mark.parametrize("magery,legal", [(1, False), (2, False), (3, True), (4, True)])
def test_character_compiler_supplies_authoritative_purchase_amounts(
    magery: int, legal: bool
) -> None:
    value = draft(magery=magery)
    value = value.model_copy(
        update={"purchases": value.purchases + (Purchase(definition_id=TARGET, amount=4),)}
    )
    result = character_engine().compile(value)
    assert result.legal is legal
    if legal:
        assert result.build is not None
        assert next(v.value for v in result.build.sheet.values if v.target == TARGET) == 11
    else:
        assert result.build is None
        assert any(d.code == "skill.prerequisite" for d in result.diagnostics)


def test_level_context_is_not_a_client_draft_field() -> None:
    value = gurps_draft().model_dump()
    value["purchased_definition_levels"] = {MAGERY: 100}
    result = character_engine().compile(value)
    assert result.build is None
    assert any(d.code == "draft.schema" for d in result.diagnostics)
