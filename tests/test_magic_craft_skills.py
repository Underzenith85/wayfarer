"""Independent magic-craft expectations, Characters B174-225."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.magic_craft_skills import BINDINGS, PROFILE, package
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import SkillSpec
from wayfarer.rules.supernatural import inventory
from wayfarer.simulation.magic_craft_skills import (
    MagicCraftCommand,
    apply_magic_craft,
    visible_history,
)
from wayfarer.simulation.resources import ResourceState
from wayfarer.world import Entity, EntityKind, World


def compiler() -> CharacterCompiler:
    base = profile_package(PROFILE)
    craft = package()
    existing = {value.id for value in base.definitions}
    dependencies = tuple(
        RuleDefinition(
            "skill:" + key,
            DefinitionKind.SKILL,
            key.title(),
            craft.sources[0].id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=SkillSpec(A.IQ, D.HARD, "B168-225"),
        )
        for key in ("naturalist", "religious-ritual")
        if "skill:" + key not in existing
    )
    combined = replace(
        base,
        id="package:test-magic-craft",
        definitions=base.definitions + craft.definitions + dependencies,
        sources=base.sources + craft.sources,
    )
    policy = CampaignPolicy(
        "policy:magic-craft",
        1,
        1000,
        1000,
        20,
        20,
        frozenset(source.id for source in combined.sources),
        technology_level=8,
        allow_supernatural=True,
    )
    rules = CampaignRules(
        combined.edition,
        (PackagePin(combined.id, combined.version, combined.digest),),
        policy.id,
        policy.version,
    )
    return CharacterCompiler(RulesCatalog((combined,)), rules, policy, statistics_profile=PROFILE)


def approved() -> ValidatedBuild:
    engine = compiler()
    result = engine.compile(gurps_draft(Purchase(definition_id="skill:thaumatology", amount=8)))
    assert result.build is not None, result.diagnostics
    return result.build


def world() -> World:
    return World(
        entities=(
            Entity("lab", EntityKind.LOCATION, "Lab"),
            Entity("mage", EntityKind.ACTOR, "Mage", "lab"),
            Entity("rival", EntityKind.ACTOR, "Rival", "lab"),
        )
    )


def test_exact_catalog_defaults_prerequisites_and_inventory() -> None:
    assert {value.key for value in BINDINGS} == {
        "alchemy",
        "herb-lore",
        "ritual-magic",
        "symbol-drawing",
        "thaumatology",
    }
    ritual = next(value for value in BINDINGS if value.key == "ritual-magic")
    assert ritual.specialty_required and ritual.defaults[0].modifier == -6
    herb = next(value for value in BINDINGS if value.key == "herb-lore")
    assert herb.technology_level_required and herb.prerequisites[0].target == "skill:naturalist"
    rows = {
        value.id: value
        for value in inventory().entries
        if value.id in {binding.id for binding in BINDINGS}
    }
    assert all(value.blockers == (191,) for value in rows.values())
    assert all(value.evidence == ("tests/test_magic_craft_skills.py",) for value in rows.values())


def test_learning_and_authorized_private_restart_safe_research() -> None:
    build = approved()
    assert {value.target: value.value for value in build.sheet.values}["skill:thaumatology"] == 10
    command = MagicCraftCommand(
        id="research",
        actor_id="mage",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:thaumatology",
        mode="research",
    )
    state, result = apply_magic_craft(
        ResourceState(),
        world(),
        build,
        command,
        authorized_actor_id="mage",
        rng=RecordedDice([3, 3, 3]),
    )
    assert result.outcome == "success" and state.revision == 1
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_magic_craft(
        restarted, world(), build, command, authorized_actor_id="mage", rng=RecordedDice([])
    ) == (restarted, result)
    assert visible_history(restarted, viewer_actor_id="mage") == (result,)
    assert visible_history(restarted, viewer_actor_id="rival") == ()


def test_authority_interruption_and_mode_boundaries_fail_closed() -> None:
    build = approved()
    command = MagicCraftCommand(
        id="research",
        actor_id="mage",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:thaumatology",
        mode="research",
    )
    with pytest.raises(AuthorizationError):
        apply_magic_craft(
            ResourceState(),
            world(),
            build,
            command,
            authorized_actor_id="rival",
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError, match="interrupted"):
        apply_magic_craft(
            ResourceState(),
            world(),
            build,
            command.model_copy(update={"interrupted": True}),
            authorized_actor_id="mage",
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError, match="Unsupported"):
        apply_magic_craft(
            ResourceState(),
            world(),
            build,
            command.model_copy(update={"mode": "invoke"}),
            authorized_actor_id="mage",
            rng=RecordedDice([]),
        )
