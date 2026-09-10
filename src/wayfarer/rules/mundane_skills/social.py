"""Whole-entry procedures for the Basic Set social skills (#345).

Intended sources: Characters, Fourth Edition B174, B183, B187, B195-196, B198,
B202, B204-205, B212, B215-216, B218-219, B223-224 with the B301-304 index, the
Voice advantage on B97, and Campaigns B359 influence rolls. Reconstructed from
model knowledge under the owner's provisional-implementation policy. Every number
declared here is pinned in ``tests/fixtures/gurps/social_skills.json``, so frozen
source verification (#336) and the printing delta audit (#191) are a data change
rather than a rewrite. No rulebook prose is bundled.

This module holds procedures, not a second mechanics engine: every roll is scored
by :mod:`wayfarer.rules.gurps_checks` and every influence attempt by the existing
:func:`wayfarer.rules.gurps_social.influence_roll`. A procedure derives its own
modifiers from trusted named conditions, never from a supplied integer; it fails
closed when a contextual prerequisite is absent; and its recorded outcome states
what happened without selecting a player's action or revealing a private motive.

Scope this module deliberately does not implement is declared, not omitted:
:attr:`Procedure.unsupported` names each transferred part and the open issue that
owns it, and :func:`unsupported_scope` publishes the whole set to the scenario,
character and LLM validators.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome, RandomSource
from wayfarer.rules.conformance import BASELINE_ID, capability, profile
from wayfarer.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    RegularContestTrace,
    quick_contest,
    regular_contest,
    success_roll,
)
from wayfarer.rules.gurps_social import (
    InfluenceSkill,
    InfluenceTrace,
    Reaction,
    ReactionModifier,
    influence_roll,
)
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D

PROFILE: Final = "gurps-basic-set-4e-2004"
REACTION_CAPABILITY: Final = "gurps.social.reaction"
INFLUENCE_CAPABILITY: Final = "gurps.social.influence"

# Owning issues for the parts of an entry these procedures do not implement.
SPECIALTIES_ISSUE: Final = 366
TECHNOLOGY_LEVEL_ISSUE: Final = 367
COERCION_ISSUE: Final = 368
ACTIVITY_ISSUE: Final = 369
AUDIENCE_ISSUE: Final = 370


class Resolution(StrEnum):
    """How an entry is decided. A procedure declares exactly one shape."""

    SUCCESS_ROLL = "success-roll"
    QUICK_CONTEST = "quick-contest"
    REGULAR_CONTEST = "regular-contest"
    INFLUENCE = "influence"


class Verdict(StrEnum):
    """The recorded result of one attempt, in the actor's frame."""

    CRITICAL_SUCCESS = "critical-success"
    SUCCESS = "success"
    TIE = "tie"
    FAILURE = "failure"
    CRITICAL_FAILURE = "critical-failure"


CONDITIONS: Final = frozenset(
    {
        "audience-perceptible",
        "audience-audible",
        "audience-visible",
        "audible-voice-trait",
        "shared-language",
        "subject-attracted",
        "credible-threat",
        "subject-restrained",
        "speaker-lips-visible",
        "social-gathering",
        "public-place",
        "matching-milieu",
        "criminal-milieu",
        "student-attentive",
        "followers-present",
    }
)
"""Named contextual facts a trusted caller may assert. Nothing else is accepted."""


@dataclass(frozen=True, slots=True)
class ConditionalModifier:
    """A modifier this procedure derives itself when a named condition holds.

    The condition is a fact about the situation; the integer belongs to the rule.
    A resolver that could supply the number instead would be supplying mechanics.
    """

    condition: str
    value: int
    reference: str


@dataclass(frozen=True, slots=True)
class Effect:
    """What one verdict records. It never chooses an action for any character."""

    id: str
    reaction_modifier: int = 0
    """Applied to later reaction rolls from the same audience."""
    aftermath_reaction: int = 0
    """Applied once the subject understands what was done to them."""
    fatigue_cost: int = 0
    requires_adjudication: bool = False


@dataclass(frozen=True, slots=True)
class UnsupportedScope:
    """A named part of an entry these procedures do not implement, with its owner."""

    id: str
    detail: str
    owner_issue: int


@dataclass(frozen=True, slots=True)
class Procedure:
    """One whole-entry social skill procedure."""

    id: str
    name: str
    reference: str
    attribute: A
    difficulty: D
    resolution: Resolution
    effects: tuple[tuple[Verdict, Effect], ...]
    required_conditions: tuple[str, ...] = ()
    modifiers: tuple[ConditionalModifier, ...] = ()
    influence_skill: InfluenceSkill | None = None
    paired: bool = False
    """Both parties must know the skill; the lower effective level decides (B198)."""
    unsupported: tuple[UnsupportedScope, ...] = ()

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Declared coverage this procedure consumes, so an unknown one fails closed."""
        if self.resolution is Resolution.INFLUENCE:
            return (REACTION_CAPABILITY, INFLUENCE_CAPABILITY)
        return (REACTION_CAPABILITY,)

    @property
    def complete(self) -> bool:
        """Whether the whole entry is implemented, not merely its roll."""
        return not self.unsupported

    def effect(self, verdict: Verdict) -> Effect:
        for declared, effect in self.effects:
            if declared is verdict:
                return effect
        raise ValidationError(f"Unreachable verdict for {self.id}: {verdict.value}")


VERDICTS: Final[MappingProxyType[Resolution, tuple[Verdict, ...]]] = MappingProxyType(
    {
        Resolution.SUCCESS_ROLL: (
            Verdict.CRITICAL_SUCCESS,
            Verdict.SUCCESS,
            Verdict.FAILURE,
            Verdict.CRITICAL_FAILURE,
        ),
        Resolution.QUICK_CONTEST: (
            Verdict.CRITICAL_SUCCESS,
            Verdict.SUCCESS,
            Verdict.TIE,
            Verdict.FAILURE,
            Verdict.CRITICAL_FAILURE,
        ),
        # A regular contest runs until exactly one side succeeds in a round, so
        # neither a tie nor a critical is reachable as its result.
        Resolution.REGULAR_CONTEST: (Verdict.SUCCESS, Verdict.FAILURE),
        # An influence roll is decided by its quick contest's winner alone (B359).
        Resolution.INFLUENCE: (Verdict.SUCCESS, Verdict.TIE, Verdict.FAILURE),
    }
)

# B97: Voice improves the social skills that carry the speaker's own voice. The
# reaction half of the advantage is already bound in rules.mundane_traits.runtime;
# this is the skill half that binding records as outstanding.
VOICE: Final = ConditionalModifier("audible-voice-trait", 2, "B97")


def _plain(prefix: str, *pairs: tuple[Verdict, str]) -> tuple[tuple[Verdict, Effect], ...]:
    return tuple((verdict, Effect(f"{prefix}-{name}")) for verdict, name in pairs)


def _contested(
    prefix: str,
    *,
    won: str,
    tied: str,
    lost: str,
    exposed: str,
    aftermath: int = 0,
) -> tuple[tuple[Verdict, Effect], ...]:
    """The five verdicts a quick contest can reach, in the actor's frame."""
    success = Effect(f"{prefix}-{won}", aftermath_reaction=aftermath)
    return (
        (Verdict.CRITICAL_SUCCESS, success),
        (Verdict.SUCCESS, success),
        (Verdict.TIE, Effect(f"{prefix}-{tied}")),
        (Verdict.FAILURE, Effect(f"{prefix}-{lost}")),
        (Verdict.CRITICAL_FAILURE, Effect(f"{prefix}-{exposed}", requires_adjudication=True)),
    )


_PROCEDURES: Final = (
    Procedure(
        "skill:acting",
        "Acting",
        "B174",
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        _contested("acting", won="believed", tied="uncertain", lost="doubted", exposed="exposed"),
        required_conditions=("audience-perceptible",),
    ),
    Procedure(
        "skill:carousing",
        "Carousing",
        "B183",
        A.HT,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("carousing-goodwill", reaction_modifier=2)),
            (Verdict.SUCCESS, Effect("carousing-goodwill", reaction_modifier=2)),
            (Verdict.FAILURE, Effect("carousing-uneventful")),
            (
                Verdict.CRITICAL_FAILURE,
                Effect(
                    "carousing-disgrace",
                    reaction_modifier=-2,
                    fatigue_cost=1,
                    requires_adjudication=True,
                ),
            ),
        ),
        required_conditions=("social-gathering", "audience-perceptible"),
        unsupported=(
            UnsupportedScope(
                "carousing-outlay",
                "The evening's cost and the hangover schedule are not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:diplomacy",
        "Diplomacy",
        "B187",
        A.IQ,
        D.HARD,
        Resolution.INFLUENCE,
        _plain(
            "diplomacy",
            (Verdict.SUCCESS, "persuaded"),
            (Verdict.TIE, "unmoved"),
            (Verdict.FAILURE, "rebuffed"),
        ),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        influence_skill="diplomacy",
    ),
    Procedure(
        "skill:fast-talk",
        "Fast-Talk",
        "B195",
        A.IQ,
        D.AVERAGE,
        Resolution.INFLUENCE,
        (
            # B195: the subject reacts at a penalty once he realizes he was had.
            (Verdict.SUCCESS, Effect("fast-talk-believed", aftermath_reaction=-3)),
            (Verdict.TIE, Effect("fast-talk-unconvinced")),
            (Verdict.FAILURE, Effect("fast-talk-caught", requires_adjudication=True)),
        ),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        influence_skill="fast-talk",
    ),
    Procedure(
        "skill:fortune-telling",
        "Fortune-Telling",
        "B196",
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        _contested(
            "fortune-telling",
            won="believed",
            tied="uncertain",
            lost="doubted",
            exposed="unmasked",
        ),
        required_conditions=("audience-perceptible", "shared-language"),
        unsupported=(
            UnsupportedScope(
                "fortune-telling-tradition",
                "The required divinatory tradition specialties are not expanded",
                SPECIALTIES_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:gesture",
        "Gesture",
        "B198",
        A.IQ,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("gesture-understood")),
            (Verdict.SUCCESS, Effect("gesture-understood")),
            (Verdict.FAILURE, Effect("gesture-unclear")),
            (Verdict.CRITICAL_FAILURE, Effect("gesture-misread", requires_adjudication=True)),
        ),
        required_conditions=("audience-visible",),
        paired=True,
    ),
    Procedure(
        "skill:interrogation",
        "Interrogation",
        "B202",
        A.IQ,
        D.AVERAGE,
        Resolution.REGULAR_CONTEST,
        _plain("interrogation", (Verdict.SUCCESS, "answered"), (Verdict.FAILURE, "withstood")),
        required_conditions=("subject-restrained", "shared-language"),
        unsupported=(
            UnsupportedScope(
                "interrogation-coercion",
                "Coercion modifiers and their injury, fatigue and reaction cost are not carried",
                COERCION_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:intimidation",
        "Intimidation",
        "B202",
        A.WILL,
        D.AVERAGE,
        Resolution.INFLUENCE,
        (
            (Verdict.SUCCESS, Effect("intimidation-cowed")),
            (Verdict.TIE, Effect("intimidation-standoff")),
            (Verdict.FAILURE, Effect("intimidation-defied", requires_adjudication=True)),
        ),
        required_conditions=("audience-perceptible", "credible-threat"),
        influence_skill="intimidation",
    ),
    Procedure(
        "skill:leadership",
        "Leadership",
        "B204",
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("leadership-followed")),
            (Verdict.SUCCESS, Effect("leadership-followed")),
            (Verdict.FAILURE, Effect("leadership-hesitant")),
            (Verdict.CRITICAL_FAILURE, Effect("leadership-refused", requires_adjudication=True)),
        ),
        required_conditions=("followers-present", "audience-audible"),
        modifiers=(VOICE,),
        unsupported=(
            UnsupportedScope(
                "leadership-group-activity",
                "Group-size modifiers and followed-group activity are not bound",
                ACTIVITY_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:lip-reading",
        "Lip Reading",
        "B205",
        A.PER,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("lip-reading-understood")),
            (Verdict.SUCCESS, Effect("lip-reading-understood")),
            (Verdict.FAILURE, Effect("lip-reading-missed")),
            (
                Verdict.CRITICAL_FAILURE,
                Effect("lip-reading-misread", requires_adjudication=True),
            ),
        ),
        required_conditions=("speaker-lips-visible", "shared-language"),
    ),
    Procedure(
        "skill:panhandling",
        "Panhandling",
        "B212",
        A.IQ,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("panhandling-given")),
            (Verdict.SUCCESS, Effect("panhandling-given")),
            (Verdict.FAILURE, Effect("panhandling-ignored")),
            (Verdict.CRITICAL_FAILURE, Effect("panhandling-run-off", requires_adjudication=True)),
        ),
        required_conditions=("public-place", "audience-perceptible"),
        unsupported=(
            UnsupportedScope(
                "panhandling-yield",
                "The money a successful attempt produces is not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:performance",
        "Performance",
        "B212",
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("performance-acclaimed")),
            (Verdict.SUCCESS, Effect("performance-received")),
            (Verdict.FAILURE, Effect("performance-flat")),
            (Verdict.CRITICAL_FAILURE, Effect("performance-jeered", requires_adjudication=True)),
        ),
        required_conditions=("audience-perceptible",),
        modifiers=(VOICE,),
        unsupported=(
            UnsupportedScope(
                "performance-audience",
                "The audience reaction and the performer's pay are not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:politics",
        "Politics",
        "B215",
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        _contested(
            "politics", won="favour", tied="deadlock", lost="refused", exposed="discredited"
        ),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
    ),
    Procedure(
        "skill:propaganda",
        "Propaganda",
        "B216",
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("propaganda-persuasive")),
            (Verdict.SUCCESS, Effect("propaganda-persuasive")),
            (Verdict.FAILURE, Effect("propaganda-ignored")),
            (Verdict.CRITICAL_FAILURE, Effect("propaganda-backfired", requires_adjudication=True)),
        ),
        required_conditions=("shared-language",),
        unsupported=(
            UnsupportedScope(
                "propaganda-technology-level",
                "The technology level decides the medium, reach and duration",
                TECHNOLOGY_LEVEL_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:public-speaking",
        "Public Speaking",
        "B216",
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("public-speaking-swayed")),
            (Verdict.SUCCESS, Effect("public-speaking-swayed")),
            (Verdict.FAILURE, Effect("public-speaking-unmoved")),
            (
                Verdict.CRITICAL_FAILURE,
                Effect("public-speaking-heckled", requires_adjudication=True),
            ),
        ),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        unsupported=(
            UnsupportedScope(
                "public-speaking-crowd",
                "The margin-scaled crowd reaction is not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:savoir-faire",
        "Savoir-Faire",
        "B218",
        A.IQ,
        D.EASY,
        Resolution.INFLUENCE,
        _plain(
            "savoir-faire",
            (Verdict.SUCCESS, "accepted"),
            (Verdict.TIE, "tolerated"),
            (Verdict.FAILURE, "snubbed"),
        ),
        required_conditions=("audience-perceptible", "matching-milieu"),
        influence_skill="savoir-faire",
        unsupported=(
            UnsupportedScope(
                "savoir-faire-milieu",
                "The required social milieu specialties are not expanded",
                SPECIALTIES_ISSUE,
            ),
        ),
    ),
    Procedure(
        "skill:sex-appeal",
        "Sex Appeal",
        "B219",
        A.HT,
        D.AVERAGE,
        Resolution.INFLUENCE,
        _plain(
            "sex-appeal",
            (Verdict.SUCCESS, "charmed"),
            (Verdict.TIE, "unaffected"),
            (Verdict.FAILURE, "rebuffed"),
        ),
        required_conditions=("audience-perceptible", "subject-attracted"),
        modifiers=(VOICE,),
        influence_skill="sex-appeal",
    ),
    Procedure(
        "skill:streetwise",
        "Streetwise",
        "B223",
        A.IQ,
        D.AVERAGE,
        Resolution.INFLUENCE,
        _plain(
            "streetwise",
            (Verdict.SUCCESS, "vouched"),
            (Verdict.TIE, "watched"),
            (Verdict.FAILURE, "shut-out"),
        ),
        required_conditions=("audience-perceptible", "criminal-milieu"),
        influence_skill="streetwise",
    ),
    Procedure(
        "skill:teaching",
        "Teaching",
        "B224",
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("teaching-taught")),
            (Verdict.SUCCESS, Effect("teaching-taught")),
            (Verdict.FAILURE, Effect("teaching-unclear")),
            (Verdict.CRITICAL_FAILURE, Effect("teaching-misled", requires_adjudication=True)),
        ),
        required_conditions=("student-attentive", "shared-language"),
        unsupported=(
            UnsupportedScope(
                "teaching-advancement",
                "The study time a successful lesson grants is not bound to advancement",
                ACTIVITY_ISSUE,
            ),
        ),
    ),
)


def _validate(procedures: tuple[Procedure, ...]) -> MappingProxyType[str, Procedure]:
    registry: dict[str, Procedure] = {}
    for entry in procedures:
        if entry.id in registry:
            raise ValidationError(f"Duplicate social procedure: {entry.id}")
        if not entry.id.startswith("skill:") or not entry.name or not entry.reference:
            raise ValidationError(f"Social procedure needs an identity and reference: {entry.id}")
        declared = tuple(verdict for verdict, _ in entry.effects)
        if declared != VERDICTS[entry.resolution]:
            raise ValidationError(f"Social procedure misses a reachable verdict: {entry.id}")
        if (entry.influence_skill is not None) != (entry.resolution is Resolution.INFLUENCE):
            raise ValidationError(f"Influence procedures need an influence skill: {entry.id}")
        if entry.paired and entry.resolution is not Resolution.SUCCESS_ROLL:
            raise ValidationError(f"Only an unopposed procedure can be paired: {entry.id}")
        conditions = entry.required_conditions + tuple(m.condition for m in entry.modifiers)
        if not set(conditions) <= CONDITIONS:
            raise ValidationError(f"Undeclared social condition: {entry.id}")
        if len(set(conditions)) != len(conditions):
            raise ValidationError(f"Duplicate social condition: {entry.id}")
        if any(scope.owner_issue < 1 or not scope.detail for scope in entry.unsupported):
            raise ValidationError(f"Unsupported scope needs an owner and detail: {entry.id}")
        registry[entry.id] = entry
    return MappingProxyType(registry)


PROCEDURES: Final = _validate(_PROCEDURES)


def procedures() -> tuple[Procedure, ...]:
    return tuple(PROCEDURES.values())


def supported(profile_id: str) -> tuple[str, ...]:
    """Identifiers a validator may accept, so an unknown one cannot be assumed.

    Every identifier here has a runnable procedure. Whether the *whole* entry is
    implemented is a separate question :func:`unsupported_scope` answers.
    """
    if profile_id != PROFILE:
        raise ValidationError("Social skill procedures require the Basic Set profile")
    profile(profile_id)
    return tuple(PROCEDURES)


def unsupported_scope() -> tuple[tuple[str, UnsupportedScope], ...]:
    """Publish every transferred part of an entry with the issue that owns it."""
    return tuple((entry.id, scope) for entry in PROCEDURES.values() for scope in entry.unsupported)


def procedure(identifier: str) -> Procedure:
    entry = PROCEDURES.get(identifier)
    if entry is None:
        raise ValidationError(f"Unknown social skill procedure: {identifier}")
    return entry


def require_procedure(profile_id: str, identifier: str) -> Procedure:
    """Resolve a procedure inside an exact profile with its coverage declared."""
    if profile_id != PROFILE:
        raise ValidationError("Social skill procedures require the Basic Set profile")
    profile(profile_id)
    entry = procedure(identifier)
    for capability_id in entry.capabilities:
        capability(capability_id)
    return entry


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


def _validate_context(entry: Procedure, context: SocialSkillContext) -> None:
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
    if context.reaction_modifiers and entry.resolution is not Resolution.INFLUENCE:
        raise ValidationError(f"Reaction modifiers reach influence rolls only: {entry.id}")


def derived_modifiers(entry: Procedure, context: SocialSkillContext) -> tuple[Modifier, ...]:
    """Modifiers the rule itself contributes, in declared order and with provenance."""
    return tuple(
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


def resolve(
    profile_id: str,
    identifier: str,
    context: SocialSkillContext,
    *,
    rng: RandomSource,
) -> SocialSkillTrace:
    """Run one attempt at a social skill procedure and record what decided it.

    Dice are drawn only from the caller's server-owned source, in the order the
    declared resolution consumes them, so a recorded receipt replays exactly.
    """
    entry = require_procedure(profile_id, identifier)
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
    assert entry.influence_skill is not None
    influence = influence_roll(
        profile_id,
        entry.influence_skill,
        context.actor_id,
        context.subject_id,
        base + sum(modifier.value for modifier in modifiers),
        context.resistance,
        context.reaction_modifiers,
        rng=rng,
    )
    winner = influence.contest.winner
    verdict = (
        Verdict.SUCCESS
        if winner == context.actor_id
        else Verdict.TIE
        if winner is None
        else Verdict.FAILURE
    )
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


ProcedureId = Literal[
    "skill:acting",
    "skill:carousing",
    "skill:diplomacy",
    "skill:fast-talk",
    "skill:fortune-telling",
    "skill:gesture",
    "skill:interrogation",
    "skill:intimidation",
    "skill:leadership",
    "skill:lip-reading",
    "skill:panhandling",
    "skill:performance",
    "skill:politics",
    "skill:propaganda",
    "skill:public-speaking",
    "skill:savoir-faire",
    "skill:sex-appeal",
    "skill:streetwise",
    "skill:teaching",
]
"""The exact inventory scope of #345, repeated as a type so a schema can pin it."""


def effect_ids() -> frozenset[str]:
    """Every outcome a procedure can record, so a validator cannot invent one."""
    return frozenset(effect.id for entry in PROCEDURES.values() for _, effect in entry.effects)
