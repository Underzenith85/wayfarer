"""Typed workshop catalog and profile previews, separate from frozen gameplay v1."""

from pydantic import Field

from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY, RegisteredProfile
from wayfarer.engine.rules.skill_types import SkillSpec
from wayfarer.engine.rules.traits import TraitRules
from wayfarer.models import Record


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
    point_cost: int | None = None
    skill: SkillSpec | None = None
    trait: TraitRules | None = None


class WorkshopOptions(Record):
    active_profile: str | None
    active_profile_version: int | None = None
    profiles: tuple[ProfileOption, ...]
    catalog: tuple[CatalogOption, ...]
    build_revision: str | None
    points_available: int
    can_approve: bool
    can_edit: bool = True


class ReviewSubmission(Record):
    draft_id: str
    actor_id: str
    owner_id: str
    name: str
    draft_revision: int
    approved: bool


class ReviewActor(Record):
    actor_id: str
    name: str


class WorkshopReviewQueue(Record):
    revision: int
    submissions: tuple[ReviewSubmission, ...]
    actors: tuple[ReviewActor, ...]


class ProfilePreviewRequest(Record):
    profile_id: str
    version: int = Field(ge=1)
    proposal: CharacterProposal


class CharacterPreviewRequest(Record):
    proposal: CharacterProposal


class PurchaseCost(Record):
    definition_id: str
    amount: int
    cost: int


class CharacterPreviewResult(Record):
    catalog: tuple[CatalogOption, ...]
    spent: int
    remaining: int
    legal: bool
    diagnostics: tuple[str, ...]
    derived: tuple[tuple[str, str], ...]
    breakdown: tuple[PurchaseCost, ...] = ()


class ProfilePreviewResult(CharacterPreviewResult):
    profile: ProfileOption


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
            id=d.id,
            name=d.name,
            kind=d.kind,
            status=d.status,
            point_cost=d.point_cost,
            skill=d.skill,
            trait=d.trait_rules,
        )
        for d in compiler.definitions.values()
    )


def preview_character(
    reviewer: PowerReviewer, proposal: CharacterProposal
) -> CharacterPreviewResult:
    """Read-only feedback uses the campaign's actual compiler, effects and review policy."""
    review = reviewer.review(proposal)
    result = review.compilation
    return CharacterPreviewResult(
        catalog=catalog_options(reviewer.compiler),
        spent=result.spent,
        remaining=result.remaining,
        legal=review.status not in ("illegal", "blocked"),
        diagnostics=tuple(d.message for d in result.diagnostics)
        + tuple(f.message for f in review.findings),
        derived=tuple((v.target, str(v.value)) for v in result.build.sheet.values)
        if result.build
        else (),
        breakdown=tuple(
            PurchaseCost(definition_id=p.definition_id, amount=p.amount, cost=p.cost)
            for p in result.build.purchases
        )
        if result.build
        else (),
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
        breakdown=tuple(
            PurchaseCost(definition_id=p.definition_id, amount=p.amount, cost=p.cost)
            for p in result.build.purchases
        )
        if result.build
        else (),
    )
