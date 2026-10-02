"""Resolving a social skill attempt against its verdicts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Final, Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome, RandomSource
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    RegularContestTrace,
    quick_contest,
    regular_contest,
    success_roll,
)
from wayfarer.engine.rules.skills.mundane.social.inventory import (
    CONDITIONS,
    Effect,
    Resolution,
    SocialProcedure,
    Verdict,
    require_procedure,
)
from wayfarer.engine.rules.skills.mundane.social.specialties import CampaignSocialSpecialties
from wayfarer.engine.rules.social.gurps_social import (
    DEFAULT_INFLUENCE_CONDITIONS,
    InfluenceConditions,
    InfluenceTrace,
    Reaction,
    ReactionModifier,
    ReactionTrace,
    influence_procedure,
    influence_roll,
    prepare_influence,
    resolve_diplomacy,
    validate_influence,
    validate_prepared_influence,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record


class InterrogationCoercion(Record):
    """A trusted B202 coercion choice and its authored consequences.

    B202 fixes the skill bonus, but not a universal physical cost or the exact
    B494 behavior penalty. Zero physical cost is valid because the entry also
    describes psychological torture.
    """

    method: Literal["severe-threats", "torture"]
    injury: int = Field(default=0, ge=0, le=100000)
    fatigue: int = Field(default=0, ge=0, le=100000)
    informed_actor_ids: tuple[Id, ...] = ()
    reaction_penalty: Literal[-2, -1] = -2

    @model_validator(mode="after")
    def unique_audience(self) -> Self:
        if len(set(self.informed_actor_ids)) != len(self.informed_actor_ids):
            raise ValueError("Coercion audiences must be unique informed actors")
        return self


@dataclass(frozen=True, slots=True)
class SocialSkillContext:
    """Trusted server context, deliberately not a serializable player command.

    ``skill`` and ``resistance`` are effective levels the caller derives from
    approved builds and pinned scenario data. ``conditions`` are named facts about
    the situation, never numbers. ``reaction_modifiers`` reach influence rolls
    through the existing reaction service and carry their own provenance.
    """

    skill: int
    resistance: int = 10
    partner_skill: int | None = None
    conditions: frozenset[str] = frozenset()
    actor_id: str = "actor"
    subject_id: str = "subject"
    reaction_modifiers: tuple[ReactionModifier, ...] = ()
    influence_conditions: InfluenceConditions = DEFAULT_INFLUENCE_CONDITIONS
    coercion: InterrogationCoercion | None = None
    callous: bool = False


@dataclass(frozen=True, slots=True)
class SocialSkillTrace:
    """One replayable attempt. Every die that decided it is inside the trace."""

    procedure_id: str
    reference: str
    resolution: Resolution
    base_skill: int
    modifiers: tuple[Modifier, ...]
    verdict: Verdict
    effect: Effect
    check: CheckTrace | None = None
    contest: QuickContestTrace | None = None
    rounds: RegularContestTrace | None = None
    influence: InfluenceTrace | None = None

    @property
    def effective_skill(self) -> int:
        return self.base_skill + sum(modifier.value for modifier in self.modifiers)

    @property
    def succeeded(self) -> bool:
        return self.verdict in (Verdict.CRITICAL_SUCCESS, Verdict.SUCCESS)

    @property
    def reaction(self) -> Reaction | None:
        """The reaction an influence attempt produced; other shapes produce none."""
        return self.influence.outcome if self.influence is not None else None


def _validate_context(entry: SocialProcedure, context: SocialSkillContext) -> None:
    for value in (context.skill, context.resistance):
        if type(value) is not int or value < 1:
            raise ValidationError(f"Social skill levels must be positive integers: {entry.id}")
    if not context.actor_id or not context.subject_id:
        raise ValidationError("Social skill attempts require stable actor and subject IDs")
    if context.actor_id == context.subject_id:
        raise ValidationError("A social skill attempt needs two distinct parties")
    if not context.conditions <= CONDITIONS:
        raise ValidationError(f"Undeclared social condition in context: {entry.id}")
    missing = [name for name in entry.required_conditions if name not in context.conditions]
    if missing:
        raise ValidationError(
            f"Social skill prerequisite is not met: {entry.id}: {', '.join(sorted(missing))}"
        )
    if entry.paired and (type(context.partner_skill) is not int or context.partner_skill < 1):
        raise ValidationError(f"Paired procedures need the other party's level: {entry.id}")
    if not entry.paired and context.partner_skill is not None:
        raise ValidationError(f"This procedure has no second party: {entry.id}")
    if not entry.influence and (
        context.reaction_modifiers or context.influence_conditions != DEFAULT_INFLUENCE_CONDITIONS
    ):
        raise ValidationError(f"Reaction modifiers reach influence rolls only: {entry.id}")
    if context.coercion is not None:
        coercion = context.coercion
        if entry.id != "skill:interrogation":
            raise ValidationError("Coercion modifiers belong to Interrogation only")
        if context.actor_id in coercion.informed_actor_ids:
            raise ValidationError("Coercion audiences must be unique informed other actors")
    if type(context.callous) is not bool or (context.callous and context.coercion is None):
        raise ValidationError("Callous modifies an explicit Interrogation coercion choice only")


def derived_modifiers(entry: SocialProcedure, context: SocialSkillContext) -> tuple[Modifier, ...]:
    """Modifiers the rule itself contributes, in declared order and with provenance."""
    modifiers = tuple(
        Modifier(
            declared.value,
            f"{entry.id}:{declared.condition}",
            declared.reference,
            BASELINE_ID,
            ModifierKind.SITUATIONAL,
        )
        for declared in entry.modifiers
        if declared.condition in context.conditions
    )
    if context.coercion is None:
        return modifiers
    modifiers += (
        Modifier(
            3 if context.coercion.method == "severe-threats" else 6,
            f"{entry.id}:{context.coercion.method}",
            "B202",
            BASELINE_ID,
            ModifierKind.SITUATIONAL,
        ),
    )
    if context.callous:
        modifiers += (
            Modifier(
                1,
                f"{entry.id}:callous-coercion",
                "B202",
                BASELINE_ID,
                ModifierKind.TRAIT,
            ),
        )
    return modifiers


def _quick_verdict(trace: QuickContestTrace, actor_id: str) -> Verdict:
    if trace.first.outcome is Outcome.CRITICAL_FAILURE:
        return Verdict.CRITICAL_FAILURE
    if trace.winner == actor_id:
        return (
            Verdict.CRITICAL_SUCCESS
            if trace.first.outcome is Outcome.CRITICAL_SUCCESS
            else Verdict.SUCCESS
        )
    return Verdict.TIE if trace.winner is None else Verdict.FAILURE


_ROLL_VERDICTS: Final[MappingProxyType[Outcome, Verdict]] = MappingProxyType(
    {
        Outcome.CRITICAL_SUCCESS: Verdict.CRITICAL_SUCCESS,
        Outcome.SUCCESS: Verdict.SUCCESS,
        Outcome.FAILURE: Verdict.FAILURE,
        Outcome.CRITICAL_FAILURE: Verdict.CRITICAL_FAILURE,
    }
)


def _influence_verdict(trace: InfluenceTrace, actor_id: str) -> Verdict:
    """B359: a trait may settle the attempt with no contest to read a winner from."""
    if trace.contest is None:
        return Verdict.SUCCESS if trace.automatic == "slave-mentality" else Verdict.FAILURE
    if trace.contest.winner == actor_id:
        return Verdict.SUCCESS
    return Verdict.TIE if trace.contest.winner is None else Verdict.FAILURE


def resolve(
    profile_id: str,
    identifier: str,
    context: SocialSkillContext,
    *,
    rng: RandomSource,
    campaign_specialties: CampaignSocialSpecialties | None = None,
) -> SocialSkillTrace:
    """Run one attempt at a social skill procedure and record what decided it.

    Dice are drawn only from the caller's server-owned source, in the order the
    declared resolution consumes them, so a recorded receipt replays exactly.
    """
    entry = require_procedure(profile_id, identifier, campaign_specialties)
    _validate_context(entry, context)
    modifiers = derived_modifiers(entry, context)
    # B198: a gesture is only as clear as the less fluent of the two parties.
    base = context.skill
    if entry.paired and context.partner_skill is not None:
        base = min(base, context.partner_skill)
    actor = Contestant(context.actor_id, base, modifiers)
    subject = Contestant(context.subject_id, context.resistance)
    if entry.resolution is Resolution.SUCCESS_ROLL:
        check = success_roll(profile_id, base, modifiers, rng=rng)
        verdict = _ROLL_VERDICTS[check.outcome]
        return SocialSkillTrace(
            entry.id,
            entry.reference,
            entry.resolution,
            base,
            modifiers,
            verdict,
            entry.effect(verdict),
            check=check,
        )
    if entry.resolution is Resolution.QUICK_CONTEST:
        contest = quick_contest(profile_id, actor, subject, rng=rng)
        verdict = _quick_verdict(contest, context.actor_id)
        return SocialSkillTrace(
            entry.id,
            entry.reference,
            entry.resolution,
            base,
            modifiers,
            verdict,
            entry.effect(verdict),
            contest=contest,
        )
    if entry.resolution is Resolution.REGULAR_CONTEST:
        rounds = regular_contest(profile_id, actor, subject, rng=rng)
        verdict = Verdict.SUCCESS if rounds.winner == context.actor_id else Verdict.FAILURE
        return SocialSkillTrace(
            entry.id,
            entry.reference,
            entry.resolution,
            base,
            modifiers,
            verdict,
            entry.effect(verdict),
            rounds=rounds,
        )
    influence = influence_roll(
        profile_id,
        influence_procedure(entry.id),
        context.actor_id,
        context.subject_id,
        base + sum(modifier.value for modifier in modifiers),
        context.resistance,
        context.reaction_modifiers,
        rng=rng,
        conditions=context.influence_conditions,
    )
    return _influence_skill_trace(entry, base, modifiers, influence, context.actor_id)


def _influence_skill_trace(
    entry: SocialProcedure,
    base: int,
    modifiers: tuple[Modifier, ...],
    influence: InfluenceTrace,
    actor_id: str,
) -> SocialSkillTrace:
    verdict = _influence_verdict(influence, actor_id)
    return SocialSkillTrace(
        entry.id,
        entry.reference,
        entry.resolution,
        base,
        modifiers,
        verdict,
        entry.effect(verdict),
        influence=influence,
    )


def validate_diplomacy_skill_context(
    profile_id: str,
    context: SocialSkillContext,
    *,
    campaign_specialties: CampaignSocialSpecialties | None = None,
) -> None:
    """Validate all prerequisite configuration before recognition/contest dice."""
    entry = require_procedure(profile_id, "skill:diplomacy", campaign_specialties)
    _validate_context(entry, context)
    validate_influence(profile_id, "diplomacy", context.influence_conditions)


def prepare_diplomacy_skill(
    profile_id: str,
    context: SocialSkillContext,
    *,
    rng: RandomSource,
    campaign_specialties: CampaignSocialSpecialties | None = None,
) -> SocialSkillTrace:
    """Resolve the B187/B359 prerequisite, leaving its fallback reaction unrolled."""
    entry = require_procedure(profile_id, "skill:diplomacy", campaign_specialties)
    _validate_context(entry, context)
    modifiers = derived_modifiers(entry, context)
    influence = prepare_influence(
        profile_id,
        "diplomacy",
        context.actor_id,
        context.subject_id,
        context.skill + sum(modifier.value for modifier in modifiers),
        context.resistance,
        context.reaction_modifiers,
        rng=rng,
        conditions=context.influence_conditions,
    )
    return _influence_skill_trace(entry, context.skill, modifiers, influence, context.actor_id)


def validate_prepared_diplomacy_skill(
    profile_id: str,
    context: SocialSkillContext,
    prepared: SocialSkillTrace,
    *,
    campaign_specialties: CampaignSocialSpecialties | None = None,
) -> None:
    """Revalidate the frozen skill and contest; neither step draws new dice."""
    entry = require_procedure(profile_id, "skill:diplomacy", campaign_specialties)
    _validate_context(entry, context)
    modifiers = derived_modifiers(entry, context)
    influence = prepared.influence
    if influence is None:
        raise ValidationError("Prepared Diplomacy skill is missing its Influence contest")
    validate_prepared_influence(
        profile_id,
        "diplomacy",
        context.actor_id,
        context.subject_id,
        context.skill + sum(modifier.value for modifier in modifiers),
        context.resistance,
        context.reaction_modifiers,
        influence,
        conditions=context.influence_conditions,
    )
    if prepared != _influence_skill_trace(
        entry, context.skill, modifiers, influence, context.actor_id
    ):
        raise ValidationError("Prepared Diplomacy skill source or preceding verdict changed")


def resolve_diplomacy_skill(
    profile_id: str,
    context: SocialSkillContext,
    prepared: SocialSkillTrace,
    selected: ReactionTrace,
    *,
    campaign_specialties: CampaignSocialSpecialties | None = None,
) -> SocialSkillTrace:
    """Keep the skill verdict and substitute only its selected fallback reaction.

    The skill effect describes the preceding contest, not the NPC's final
    response. Consumers of reaction bands must read the combined ``reaction``.
    """
    validate_prepared_diplomacy_skill(
        profile_id, context, prepared, campaign_specialties=campaign_specialties
    )
    assert prepared.influence is not None
    influence = resolve_diplomacy(
        profile_id,
        context.actor_id,
        context.subject_id,
        prepared.effective_skill,
        context.resistance,
        context.reaction_modifiers,
        prepared.influence,
        selected,
        conditions=context.influence_conditions,
    )
    return replace(prepared, influence=influence)
