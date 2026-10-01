"""Synthetic builds for college command-boundary tests, not learning evidence."""

from dataclasses import replace

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
from wayfarer.engine.rules.magic.colleges import PROFILE
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec


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
