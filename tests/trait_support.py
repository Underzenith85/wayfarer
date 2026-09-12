"""Shared compiler construction for the independently owned trait-family suites."""

from dataclasses import replace

from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.traits.base import TraitOptions


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def trait_compiler(
    name: str,
    profile: str,
    *packages: RulesPackage,
    hooks: frozenset[str] = frozenset(),
) -> CharacterCompiler:
    """A supernatural-enabled compiler over the profile plus the named trait packages."""
    base = profile_package(profile)
    combined = replace(
        base,
        id=f"package:test-{name}",
        definitions=base.definitions
        + tuple(definition for package in packages for definition in package.definitions),
    )
    policy = CampaignPolicy(
        f"policy:{name}",
        1,
        10000,
        10000,
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
    return CharacterCompiler(
        RulesCatalog((combined,)),
        rules,
        policy,
        statistics_profile=profile,
        trait_runtime_hooks=hooks,
    )


def approved_build(
    engine: CharacterCompiler, *purchases: Purchase
) -> tuple[ValidatedBuild, CharacterCompiler]:
    """The validated build for a draft of the standard attributes plus `purchases`."""
    result = engine.compile(gurps_draft(*purchases))
    assert result.build is not None, result.diagnostics
    return result.build, engine
