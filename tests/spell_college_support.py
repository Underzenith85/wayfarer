"""Synthetic builds for college command-boundary tests, not learning evidence."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    ImplementationStatus,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.colleges import PROFILE, CollegeSpellBinding
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.magic.colleges import CollegeSpellCommand, apply_college_spell
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError


def approved_spell(definition: RulesPackage, spell_id: str) -> ValidatedBuild:
    # These tests exercise command receipts only. Production learning validation
    # has independent tests and must never use this synthetic specification.
    definition = replace(
        definition,
        definitions=tuple(
            replace(
                entry,
                status=ImplementationStatus.IMPLEMENTED,
                skill=SkillSpec(ControllingAttribute.IQ, Difficulty.HARD, "test:command-boundary"),
            )
            if entry.id == spell_id
            else entry
            for entry in definition.definitions
        ),
    )
    base = profile_package(PROFILE)
    combined = replace(
        base,
        id="package:test-" + spell_id.removeprefix("spell:"),
        definitions=base.definitions + definition.definitions,
        sources=base.sources + definition.sources,
    )
    policy = CampaignPolicy(
        "policy:test-spell-college",
        1,
        1000,
        1000,
        20,
        20,
        frozenset(source.id for source in combined.sources),
        allow_supernatural=True,
    )
    rules = CampaignRules(
        combined.edition,
        (PackagePin(combined.id, combined.version, combined.digest),),
        policy.id,
        policy.version,
    )
    engine = CharacterCompiler(RulesCatalog((combined,)), rules, policy, statistics_profile=PROFILE)
    result = engine.compile(gurps_draft(Purchase(definition_id=spell_id, amount=4)))
    assert result.build is not None, result.diagnostics
    return result.build


def assert_unsupported_cast(
    package: RulesPackage, bindings: tuple[CollegeSpellBinding, ...], spell_id: str
) -> None:
    """A check-only college command must not consume dice, energy, or receipts."""
    build = approved_spell(package, spell_id)
    state = ResourceState()
    world = World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("mage", EntityKind.ACTOR, "Mage", "room"),
        )
    )
    command = CollegeSpellCommand(
        id="unsupported",
        actor_id="mage",
        expected_revision=0,
        build_revision=build.revision,
        spell_id=spell_id,
    )
    with pytest.raises(ValidationError, match="no executable effect"):
        apply_college_spell(
            state, world, build, command, bindings, authorized_actor_id="mage", rng=RecordedDice([])
        )
    assert state == ResourceState()
    with pytest.raises(AuthorizationError):
        apply_college_spell(
            state,
            world,
            build,
            command,
            bindings,
            authorized_actor_id="other",
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError):
        apply_college_spell(
            state,
            world,
            build,
            command.model_copy(update={"expected_revision": 1}),
            bindings,
            authorized_actor_id="mage",
            rng=RecordedDice([]),
        )
