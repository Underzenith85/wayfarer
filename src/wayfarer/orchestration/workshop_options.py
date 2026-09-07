"""Typed workshop catalog and profile previews, separate from frozen gameplay v1."""

from pydantic import Field

from wayfarer.character.compiler import CharacterCompiler
from wayfarer.character.power import CharacterProposal
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus
from wayfarer.rules.profiles import DEFAULT_REGISTRY, RegisteredProfile
from wayfarer.rules.skill_types import SkillSpec
from wayfarer.rules.traits import TraitRules
from wayfarer.simulation.resources import Record


class ProfileOption(Record):
    id: str
    version: int
    title: str
    supported: bool
    blockers: tuple[str, ...]


class CatalogOption(Record):
    id: str
    name: str
    kind: DefinitionKind
    status: ImplementationStatus
    skill: SkillSpec | None = None
    trait: TraitRules | None = None


class WorkshopOptions(Record):
    active_profile: str | None
    profiles: tuple[ProfileOption, ...]
    catalog: tuple[CatalogOption, ...]
    build_revision: str | None
    points_available: int
    can_approve: bool


class ProfilePreviewRequest(Record):
    profile_id: str
    version: int = Field(ge=1)
    proposal: CharacterProposal


class ProfilePreviewResult(Record):
    profile: ProfileOption
    catalog: tuple[CatalogOption, ...]
    spent: int
    remaining: int
    legal: bool
    diagnostics: tuple[str, ...]
    derived: tuple[tuple[str, str], ...]


def profile_option(profile: RegisteredProfile) -> ProfileOption:
    return ProfileOption(
        id=profile.id,
        version=profile.version,
        title=profile.title,
        supported=profile.supported,
        blockers=profile.unverified_capabilities,
    )


def catalog_options(compiler: CharacterCompiler) -> tuple[CatalogOption, ...]:
    return tuple(
        CatalogOption(
            id=d.id, name=d.name, kind=d.kind, status=d.status, skill=d.skill, trait=d.trait_rules
        )
        for d in compiler.definitions.values()
    )


def preview_profile(request: ProfilePreviewRequest) -> ProfilePreviewResult:
    selected = DEFAULT_REGISTRY.get(request.profile_id, request.version)
    compiler = CharacterCompiler(
        selected.catalog,
        selected.rules,
        selected.policy,
        statistics_profile=selected.conformance_profile_id,
    )
    result = compiler.compile(request.proposal.draft)
    return ProfilePreviewResult(
        profile=profile_option(selected),
        catalog=catalog_options(compiler),
        spent=result.spent,
        remaining=result.remaining,
        legal=result.build is not None,
        diagnostics=tuple(d.message for d in result.diagnostics),
        derived=tuple((v.target, str(v.value)) for v in result.build.sheet.values)
        if result.build
        else (),
    )
