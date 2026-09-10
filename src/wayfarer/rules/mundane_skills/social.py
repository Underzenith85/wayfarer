"""Executable social skill procedures (#345); unbound rows stay blocked.

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B174, B183, B187, B195-196, B198, B202, B204-205, B212, B215-216, B218-219,
B223-224 with the B301-304 index, the Voice advantage on B97, and Campaigns B359
influence rolls. The frozen first-printing/2007-01-26 errata delta stays #191's
blocker on every row, so a bound procedure is reported as implemented and is
still not certified. Every number declared here is pinned in
`tests/fixtures/gurps/social_skills.json`. No rulebook prose is bundled.

Recording a skill never makes it playable. A row is implemented only when this
module binds it to a service that already resolves it: `rules.gurps_checks` for
success rolls and contests, and `rules.gurps_social.influence_roll` for the six
B359 influence skills. There is no second engine here. Every other listed row
keeps its recorded blockers and names the concrete open child issue that owns
them: #366 social specialties, #367 the Propaganda technology level, #353
conditional and alternative defaults.

A procedure declares the shape that decides it, the contextual conditions it
cannot proceed without, the modifiers it derives itself, and a named effect for
each verdict that shape can reach. Conditions are facts about the situation; the
integer each is worth belongs to the rule, so no resolver supplies mechanics. A
recorded outcome states what happened without selecting a player's action or
revealing a private motive. Scope a bound procedure still does not carry is
declared in :attr:`SocialProcedure.unsupported` with the issue that owns it and
published by :func:`unsupported_scope`, never silently omitted.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome, RandomSource
from wayfarer.rules.conformance import BASELINE_ID, CoverageStatus, capability, profile
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    RegularContestTrace,
    quick_contest,
    regular_contest,
    success_roll,
)
from wayfarer.rules.gurps_social import (
    DEFAULT_INFLUENCE_CONDITIONS,
    InfluenceConditions,
    InfluenceTrace,
    Reaction,
    ReactionModifier,
    influence_procedure,
    influence_roll,
)
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import SkillDefault, SkillSpec

PROFILE: Final = "gurps-basic-set-4e-2004"
OWNER: Final = 345
CAPABILITIES: Final = ("gurps.social.reaction", "gurps.social.skill_procedures")
INFLUENCE_CAPABILITY: Final = "gurps.social.influence"
DISPATCH: Final = "social.skill-procedure"

RUNTIME_PROCEDURE: Final = "runtime-procedure"
SPECIALTY_EXPANSION: Final = "specialty-expansion"
CONDITIONAL_DEFAULTS: Final = "conditional-or-skill-defaults"
TECHNOLOGY_LEVEL: Final = "technology-level-context"

# Owning issues for the parts of an entry these procedures do not carry.
SPECIALTIES_ISSUE: Final = 366
TECHNOLOGY_LEVEL_ISSUE: Final = 367
COERCION_ISSUE: Final = 368
ACTIVITY_ISSUE: Final = 369
AUDIENCE_ISSUE: Final = 370
DEFAULTS_ISSUE: Final = 383


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
    """A named part of a bound entry this module does not carry, with its owner."""

    id: str
    detail: str
    owner_issue: int


@dataclass(frozen=True, slots=True)
class SocialProcedure:
    """One accounted-for social row and the dispatch it does or does not have."""

    id: str
    name: str
    page: int
    attribute: A
    difficulty: D
    resolution: Resolution
    effects: tuple[tuple[Verdict, Effect], ...] = ()
    defaults: tuple[SkillDefault, ...] = ()
    required_conditions: tuple[str, ...] = ()
    modifiers: tuple[ConditionalModifier, ...] = ()
    paired: bool = False
    """Both parties must know the skill; the lower effective level decides (B198)."""
    resolved: tuple[str, ...] = ()
    # Blockers this issue does not close, each mapped to the concrete open child
    # that owns it. A transferred blocker without an owner is a coverage failure.
    transferred: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    # Scope a *bound* row still does not carry. Unlike a transferred blocker this
    # does not stop the procedure running; it names what its outcome leaves out.
    unsupported: tuple[UnsupportedScope, ...] = ()

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(self.transferred)

    @property
    def owners(self) -> tuple[int, ...]:
        transferred = tuple(i for owners in self.transferred.values() for i in owners)
        return tuple(dict.fromkeys(transferred + tuple(s.owner_issue for s in self.unsupported)))

    @property
    def implemented(self) -> bool:
        """A bound dispatch executes; recording a procedure never implements it."""
        return RUNTIME_PROCEDURE in self.resolved

    @property
    def dispatchable(self) -> bool:
        return self.implemented

    @property
    def dispatch(self) -> str | None:
        return DISPATCH if self.dispatchable else None

    @property
    def complete(self) -> bool:
        """Whether the whole entry is carried, not merely its roll."""
        return self.implemented and not self.unsupported

    @property
    def influence(self) -> bool:
        return self.resolution is Resolution.INFLUENCE

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Declared coverage this procedure consumes, so an unknown one fails closed."""
        return CAPABILITIES + ((INFLUENCE_CAPABILITY,) if self.influence else ())

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    def spec(self) -> SkillSpec:
        return SkillSpec(self.attribute, self.difficulty, self.reference, self.defaults)

    def definition(self) -> RuleDefinition:
        if not self.dispatchable:
            raise ValidationError(f"Social skill has no bound dispatch: {self.id}")
        return RuleDefinition(
            self.id,
            DefinitionKind.SKILL,
            self.name,
            source(PROFILE).id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", DISPATCH),
            skill=self.spec(),
        )

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
        # An influence roll is decided by its quick contest's winner, or by a
        # B359 trait that settles it without one.
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
    prefix: str, *, won: str, tied: str, lost: str, exposed: str
) -> tuple[tuple[Verdict, Effect], ...]:
    """The five verdicts a quick contest can reach, in the actor's frame."""
    success = Effect(f"{prefix}-{won}")
    return (
        (Verdict.CRITICAL_SUCCESS, success),
        (Verdict.SUCCESS, success),
        (Verdict.TIE, Effect(f"{prefix}-{tied}")),
        (Verdict.FAILURE, Effect(f"{prefix}-{lost}")),
        (Verdict.CRITICAL_FAILURE, Effect(f"{prefix}-{exposed}", requires_adjudication=True)),
    )


def _unopposed(
    prefix: str, *, won: str, lost: str, botched: str
) -> tuple[tuple[Verdict, Effect], ...]:
    success = Effect(f"{prefix}-{won}")
    return (
        (Verdict.CRITICAL_SUCCESS, success),
        (Verdict.SUCCESS, success),
        (Verdict.FAILURE, Effect(f"{prefix}-{lost}")),
        (Verdict.CRITICAL_FAILURE, Effect(f"{prefix}-{botched}", requires_adjudication=True)),
    )


_ROWS: Final = (
    SocialProcedure(
        "skill:acting",
        "Acting",
        174,
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        _contested("acting", won="believed", tied="uncertain", lost="doubted", exposed="exposed"),
        (
            SkillDefault(A.IQ, -5),
            SkillDefault("skill:performance", -2),
            SkillDefault("skill:public-speaking", -5),
        ),
        required_conditions=("audience-perceptible",),
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:carousing",
        "Carousing",
        183,
        A.HT,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        (
            # B183: an evening spent well is worth +2 on later reactions.
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
        (SkillDefault(A.HT, -4),),
        required_conditions=("social-gathering", "audience-perceptible"),
        resolved=(RUNTIME_PROCEDURE,),
        unsupported=(
            UnsupportedScope(
                "carousing-outlay",
                "The evening's cost and the hangover schedule are not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:diplomacy",
        "Diplomacy",
        187,
        A.IQ,
        D.HARD,
        Resolution.INFLUENCE,
        _plain(
            "diplomacy",
            (Verdict.SUCCESS, "persuaded"),
            (Verdict.TIE, "unmoved"),
            (Verdict.FAILURE, "rebuffed"),
        ),
        (SkillDefault(A.IQ, -6), SkillDefault("skill:politics", -6)),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:fast-talk",
        "Fast-Talk",
        195,
        A.IQ,
        D.AVERAGE,
        Resolution.INFLUENCE,
        (
            # B195: the subject reacts at a penalty once he realizes he was had.
            (Verdict.SUCCESS, Effect("fast-talk-believed", aftermath_reaction=-3)),
            (Verdict.TIE, Effect("fast-talk-unconvinced")),
            (Verdict.FAILURE, Effect("fast-talk-caught", requires_adjudication=True)),
        ),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
    ),
    SocialProcedure(
        "skill:fortune-telling",
        "Fortune-Telling",
        196,
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        defaults=(SkillDefault(A.IQ, -5),),
        transferred={
            RUNTIME_PROCEDURE: (SPECIALTIES_ISSUE,),
            SPECIALTY_EXPANSION: (SPECIALTIES_ISSUE,),
            CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,),
        },
    ),
    SocialProcedure(
        "skill:gesture",
        "Gesture",
        198,
        A.IQ,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        _unopposed("gesture", won="understood", lost="unclear", botched="misread"),
        (SkillDefault(A.IQ, -4),),
        required_conditions=("audience-visible",),
        paired=True,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:interrogation",
        "Interrogation",
        202,
        A.IQ,
        D.AVERAGE,
        Resolution.REGULAR_CONTEST,
        _plain("interrogation", (Verdict.SUCCESS, "answered"), (Verdict.FAILURE, "withstood")),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("subject-restrained", "shared-language"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
        unsupported=(
            UnsupportedScope(
                "interrogation-coercion",
                "Coercion modifiers and their injury, fatigue and reaction cost are not carried",
                COERCION_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:intimidation",
        "Intimidation",
        202,
        A.WILL,
        D.AVERAGE,
        Resolution.INFLUENCE,
        (
            (Verdict.SUCCESS, Effect("intimidation-cowed")),
            (Verdict.TIE, Effect("intimidation-standoff")),
            (Verdict.FAILURE, Effect("intimidation-defied", requires_adjudication=True)),
        ),
        (SkillDefault(A.WILL, -5),),
        required_conditions=("audience-perceptible", "credible-threat"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
    ),
    SocialProcedure(
        "skill:leadership",
        "Leadership",
        204,
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        _unopposed("leadership", won="followed", lost="hesitant", botched="refused"),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("followers-present", "audience-audible"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
        unsupported=(
            UnsupportedScope(
                "leadership-group-activity",
                "Group-size modifiers and followed-group activity are not bound",
                ACTIVITY_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:lip-reading",
        "Lip Reading",
        205,
        A.PER,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        _unopposed("lip-reading", won="understood", lost="missed", botched="misread"),
        (SkillDefault(A.PER, -10),),
        required_conditions=("speaker-lips-visible", "shared-language"),
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:panhandling",
        "Panhandling",
        212,
        A.IQ,
        D.EASY,
        Resolution.SUCCESS_ROLL,
        _unopposed("panhandling", won="given", lost="ignored", botched="run-off"),
        (SkillDefault(A.IQ, -4),),
        required_conditions=("public-place", "audience-perceptible"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
        unsupported=(
            UnsupportedScope(
                "panhandling-yield",
                "The money a successful attempt produces is not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:performance",
        "Performance",
        212,
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        (
            (Verdict.CRITICAL_SUCCESS, Effect("performance-acclaimed")),
            (Verdict.SUCCESS, Effect("performance-received")),
            (Verdict.FAILURE, Effect("performance-flat")),
            (Verdict.CRITICAL_FAILURE, Effect("performance-jeered", requires_adjudication=True)),
        ),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("audience-perceptible",),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
        unsupported=(
            UnsupportedScope(
                "performance-audience",
                "The audience reaction and the performer's pay are not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:politics",
        "Politics",
        215,
        A.IQ,
        D.AVERAGE,
        Resolution.QUICK_CONTEST,
        _contested(
            "politics", won="favour", tied="deadlock", lost="refused", exposed="discredited"
        ),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,)},
    ),
    SocialProcedure(
        "skill:propaganda",
        "Propaganda",
        216,
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        defaults=(SkillDefault(A.IQ, -5),),
        transferred={
            RUNTIME_PROCEDURE: (TECHNOLOGY_LEVEL_ISSUE,),
            TECHNOLOGY_LEVEL: (TECHNOLOGY_LEVEL_ISSUE,),
            CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,),
        },
    ),
    SocialProcedure(
        "skill:public-speaking",
        "Public Speaking",
        216,
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        _unopposed("public-speaking", won="swayed", lost="unmoved", botched="heckled"),
        (
            SkillDefault(A.IQ, -5),
            SkillDefault("skill:acting", -5),
            SkillDefault("skill:performance", -2),
            SkillDefault("skill:politics", -5),
        ),
        required_conditions=("audience-audible", "shared-language"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
        unsupported=(
            UnsupportedScope(
                "public-speaking-crowd",
                "The margin-scaled crowd reaction is not carried",
                AUDIENCE_ISSUE,
            ),
        ),
    ),
    SocialProcedure(
        "skill:savoir-faire",
        "Savoir-Faire",
        218,
        A.IQ,
        D.EASY,
        Resolution.INFLUENCE,
        defaults=(SkillDefault(A.IQ, -4),),
        transferred={
            RUNTIME_PROCEDURE: (SPECIALTIES_ISSUE,),
            SPECIALTY_EXPANSION: (SPECIALTIES_ISSUE,),
            CONDITIONAL_DEFAULTS: (DEFAULTS_ISSUE,),
        },
    ),
    SocialProcedure(
        "skill:sex-appeal",
        "Sex Appeal",
        219,
        A.HT,
        D.AVERAGE,
        Resolution.INFLUENCE,
        _plain(
            "sex-appeal",
            (Verdict.SUCCESS, "charmed"),
            (Verdict.TIE, "unaffected"),
            (Verdict.FAILURE, "rebuffed"),
        ),
        (SkillDefault(A.HT, -3),),
        required_conditions=("audience-perceptible", "subject-attracted"),
        modifiers=(VOICE,),
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:streetwise",
        "Streetwise",
        223,
        A.IQ,
        D.AVERAGE,
        Resolution.INFLUENCE,
        _plain(
            "streetwise",
            (Verdict.SUCCESS, "vouched"),
            (Verdict.TIE, "watched"),
            (Verdict.FAILURE, "shut-out"),
        ),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("audience-perceptible", "criminal-milieu"),
        resolved=(RUNTIME_PROCEDURE,),
    ),
    SocialProcedure(
        "skill:teaching",
        "Teaching",
        224,
        A.IQ,
        D.AVERAGE,
        Resolution.SUCCESS_ROLL,
        _unopposed("teaching", won="taught", lost="unclear", botched="misled"),
        (SkillDefault(A.IQ, -5),),
        required_conditions=("student-attentive", "shared-language"),
        resolved=(RUNTIME_PROCEDURE,),
        unsupported=(
            UnsupportedScope(
                "teaching-advancement",
                "The study time a successful lesson grants is not bound to advancement",
                ACTIVITY_ISSUE,
            ),
        ),
    ),
)


def _validate(rows: tuple[SocialProcedure, ...]) -> MappingProxyType[str, SocialProcedure]:
    registry: dict[str, SocialProcedure] = {}
    for entry in rows:
        if entry.id in registry:
            raise ValidationError(f"Duplicate social procedure: {entry.id}")
        if not entry.id.startswith("skill:") or not entry.name or not 168 <= entry.page <= 233:
            raise ValidationError(f"Social procedure needs an identity and reference: {entry.id}")
        declared = tuple(verdict for verdict, _ in entry.effects)
        if declared != (VERDICTS[entry.resolution] if entry.implemented else ()):
            raise ValidationError(f"Social procedure misses a reachable verdict: {entry.id}")
        if entry.paired and entry.resolution is not Resolution.SUCCESS_ROLL:
            raise ValidationError(f"Only an unopposed procedure can be paired: {entry.id}")
        if entry.influence and entry.implemented:
            influence_procedure(entry.id)
        conditions = entry.required_conditions + tuple(m.condition for m in entry.modifiers)
        if not set(conditions) <= CONDITIONS:
            raise ValidationError(f"Undeclared social condition: {entry.id}")
        if len(set(conditions)) != len(conditions):
            raise ValidationError(f"Duplicate social condition: {entry.id}")
        if entry.implemented == (RUNTIME_PROCEDURE in entry.transferred):
            raise ValidationError(f"A row is either bound or transferred: {entry.id}")
        if not entry.implemented and (entry.required_conditions or entry.unsupported):
            raise ValidationError(f"An unbound row declares no procedure detail: {entry.id}")
        if any(not owners for owners in entry.transferred.values()):
            raise ValidationError(f"Transferred blocker names no owner: {entry.id}")
        if any(scope.owner_issue < 1 or not scope.detail for scope in entry.unsupported):
            raise ValidationError(f"Unsupported scope needs an owner and detail: {entry.id}")
        registry[entry.id] = entry
    return MappingProxyType(registry)


PROCEDURES: Final = _validate(_ROWS)
"""Every listed social row, bound or transferred, keyed by its pinned skill ID."""


def procedures() -> tuple[SocialProcedure, ...]:
    return tuple(PROCEDURES.values())


def definitions() -> tuple[RuleDefinition, ...]:
    """Dispatchable social skills for a new package pin; unbound rows are absent."""
    return tuple(entry.definition() for entry in _ROWS if entry.dispatchable)


def supported(profile_id: str) -> tuple[str, ...]:
    """Identifiers a validator may accept, so an unknown one cannot be assumed."""
    if profile_id != PROFILE:
        raise ValidationError("Social skill procedures require the Basic Set profile")
    profile(profile_id)
    return tuple(entry.id for entry in _ROWS if entry.dispatchable)


def unsupported_scope() -> tuple[tuple[str, UnsupportedScope], ...]:
    """Publish every part of a bound entry this module leaves to another issue."""
    return tuple((entry.id, scope) for entry in _ROWS for scope in entry.unsupported)


def procedure(identifier: str) -> SocialProcedure:
    entry = PROCEDURES.get(identifier)
    if entry is None:
        raise ValidationError(f"Unknown social skill procedure: {identifier}")
    return entry


def require_capability(profile_id: str, capability_id: str) -> None:
    """Use the registry, never a typed context or a manual ruling, to claim support."""
    declared = capability(capability_id)
    if capability_id not in profile(profile_id).required_capabilities:
        raise ValidationError(f"Rules capability outside profile: {capability_id}")
    if declared.status is CoverageStatus.ABSENT:
        raise ValidationError(f"Rules capability has no coverage: {capability_id}")


def require_procedure(profile_id: str, identifier: str) -> SocialProcedure:
    """Fail closed before dice when a caller claims an unbound social skill."""
    if profile_id != PROFILE:
        raise ValidationError(
            f"Social skill procedure requires the exact Basic Set profile: {identifier}"
        )
    entry = procedure(identifier)
    if not entry.dispatchable:
        raise ValidationError(
            f"Social skill procedure is unsupported: {identifier}: "
            + ", ".join(
                f"{blocker} (" + ", ".join(f"#{issue}" for issue in owners) + ")"
                for blocker, owners in entry.transferred.items()
            )
        )
    for capability_id in entry.capabilities:
        require_capability(profile_id, capability_id)
    return entry


def effect_ids() -> frozenset[str]:
    """Every outcome a procedure can record, so a validator cannot invent one."""
    return frozenset(effect.id for entry in _ROWS for _, effect in entry.effects)


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


def derived_modifiers(entry: SocialProcedure, context: SocialSkillContext) -> tuple[Modifier, ...]:
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
    verdict = _influence_verdict(influence, context.actor_id)
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
