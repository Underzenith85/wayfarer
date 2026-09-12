"""Shared test construction for independently owned Basic Set spell colleges."""

from dataclasses import replace

from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.rules.spell_colleges import PROFILE


def approved_spell(definition: RulesPackage, spell_id: str) -> ValidatedBuild:
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
