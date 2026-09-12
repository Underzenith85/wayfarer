"""Shared execution contract for source-bound mundane skill procedures.

Each family catalog declares the task and outcome of every row it owns.  This
module supplies only the common, receipt-producing check/contest machinery; it
does not choose a task or turn a generic skill check into implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    RegularContestTrace,
    RepeatedAttemptPolicy,
    quick_contest,
    regular_contest,
    replay_quick_contest,
    replay_regular_contest,
    replay_success,
    success_roll,
)
from wayfarer.engine.rules.skills.mundane.source_defaults import (
    recorded_blockers,
    recorded_reference,
    recorded_spec,
    recorded_specialties,
    recorded_template,
    recorded_variable_subject,
)
from wayfarer.engine.rules.types.skill import SkillSpec, TechniqueTemplate
from wayfarer.errors import ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"
RUNTIME_PROCEDURE: Final = "runtime-procedure"
COMBAT_PROCEDURE: Final = "combat-procedure"
TECHNIQUE_EXPANSION: Final = "technique-expansion"


class Resolution(StrEnum):
    SUCCESS_ROLL = "success-roll"
    QUICK_CONTEST = "quick-contest"
    REGULAR_CONTEST = "regular-contest"


@dataclass(frozen=True, slots=True)
class Task:
    """A procedure's exact service handoff and observable result."""

    dispatch: str
    effect: str
    policy: RepeatedAttemptPolicy
    unit: str
    resolution: Resolution = Resolution.SUCCESS_ROLL
    required_context: tuple[str, ...] = ()
    allowed_modifiers: frozenset[str] = frozenset()
    units_per_margin: int = 0
    unit_cap: int = 0

    def units(self, margin: int) -> int:
        if margin < 0:
            return 0
        value = 1 + self.units_per_margin * margin
        return min(value, self.unit_cap) if self.unit_cap else value


@dataclass(frozen=True, slots=True)
class Procedure:
    """One explicit row binding, reconciled against the frozen source row."""

    id: str
    name: str
    owner: int
    task: Task | None
    resolved: tuple[str, ...]
    transferred: dict[str, tuple[int, ...]] = field(default_factory=dict)
    specialties: tuple[str, ...] = ()
    subject: str | None = None
    template: TechniqueTemplate | None = None

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(self.transferred)

    @property
    def implemented(self) -> bool:
        return (
            RUNTIME_PROCEDURE in self.resolved
            or COMBAT_PROCEDURE in self.resolved
            or (self.task is not None and not recorded_blockers(self.id))
        )

    @property
    def dispatchable(self) -> bool:
        return self.implemented and self.task is not None and not self.specialties

    @property
    def dispatch(self) -> str | None:
        return self.task.dispatch if self.dispatchable and self.task else None

    @property
    def reference(self) -> str:
        spec = self.spec()
        if spec is not None:
            return spec.reference
        template = self.template
        if template is None:
            return recorded_reference(self.id)
        return "B230-233"

    def spec(self) -> SkillSpec | None:
        return recorded_spec(self.id)

    def definition(self) -> RuleDefinition:
        spec = self.spec()
        if not self.dispatchable or spec is None:
            raise ValidationError(f"Mundane procedure has no concrete definition: {self.id}")
        assert self.task is not None
        return RuleDefinition(
            self.id,
            DefinitionKind.SKILL,
            self.name,
            source(PROFILE).id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", self.task.dispatch),
            skill=spec,
        )


def bind(identifier: str, name: str, owner: int, task: Task | None) -> Procedure:
    """Bind exactly the blockers recorded for a row, leaving none implicit."""
    blockers = recorded_blockers(identifier)
    resolvable = {RUNTIME_PROCEDURE, COMBAT_PROCEDURE}
    spec = recorded_spec(identifier)
    if task is not None and (
        (spec is not None and spec.technique is not None)
        or recorded_template(identifier) is not None
    ):
        resolvable.add(TECHNIQUE_EXPANSION)
    resolved = tuple(value for value in blockers if value in resolvable)
    transferred: dict[str, tuple[int, ...]] = {
        value: (owner,) for value in blockers if value not in resolvable
    }
    return Procedure(
        identifier,
        name,
        owner,
        task,
        resolved,
        transferred,
        recorded_specialties(identifier),
        recorded_variable_subject(identifier),
        recorded_template(identifier),
    )


@dataclass(frozen=True, slots=True)
class Performer:
    skill_id: str
    level: int
    parent_level: int | None = None
    parent_skill_id: str | None = None


@dataclass(frozen=True, slots=True)
class Situation:
    conditions: frozenset[str] = frozenset()
    resistance: int | None = None
    modifiers: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    situational: tuple[Modifier, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcedureResult:
    procedure_id: str
    reference: str
    dispatch: str
    effect: str
    policy: RepeatedAttemptPolicy
    unit: str
    units: int
    check: CheckTrace | None = None
    contest: QuickContestTrace | None = None
    rounds: RegularContestTrace | None = None

    @property
    def succeeded(self) -> bool:
        if self.check is not None:
            return self.check.outcome.succeeded
        if self.contest is not None:
            return self.contest.winner == "actor"
        assert self.rounds is not None
        return self.rounds.winner == "actor"


def _target(procedure: Procedure, performer: Performer) -> int:
    template = procedure.template
    spec = procedure.spec()
    technique = spec.technique if spec else None
    if template is None and technique is None:
        return performer.level
    if performer.parent_level is None or performer.parent_skill_id is None:
        raise ValidationError(f"Technique requires an explicit parent: {procedure.id}")
    if template is not None:
        valid_parent = performer.parent_skill_id in template.parents or (
            template.parent_family is not None
            and performer.parent_skill_id.startswith(template.parent_family + "-")
        )
        floor = performer.parent_level + template.default_modifier
        ceiling = performer.parent_level + template.maximum_modifier
    else:
        assert technique is not None
        valid_parent = performer.parent_skill_id == technique.parent
        floor = performer.parent_level + technique.default_modifier
        ceiling = performer.parent_level + technique.maximum_modifier
    if not valid_parent:
        raise ValidationError(f"Technique parent does not match its source entry: {procedure.id}")
    if not floor <= performer.level <= ceiling:
        raise ValidationError(f"Technique level outside its parent-specific range: {procedure.id}")
    return performer.level


def require_procedure(
    procedures: Mapping[str, Procedure],
    skill_id: str,
    *,
    profile_id: str,
) -> Procedure:
    procedure = procedures.get(skill_id)
    if procedure is None:
        raise ValidationError(f"Skill is outside this procedure family: {skill_id}")
    if profile_id != PROFILE:
        raise ValidationError(f"Mundane skill requires the exact Basic Set profile: {skill_id}")
    if procedure.specialties:
        raise ValidationError(
            f"Skill family requires a concrete specialty: {skill_id}: "
            + ", ".join(procedure.specialties)
        )
    if not procedure.dispatchable:
        raise ValidationError(f"Mundane skill procedure is unsupported: {skill_id}")
    return procedure


def attempt(
    procedures: Mapping[str, Procedure],
    performer: Performer,
    situation: Situation,
    *,
    rng: RandomSource,
    profile_id: str = PROFILE,
) -> ProcedureResult:
    """Execute a catalog-selected procedure and return a replayable receipt."""
    procedure = require_procedure(procedures, performer.skill_id, profile_id=profile_id)
    assert procedure.task is not None and procedure.dispatch is not None
    task = procedure.task
    missing = set(task.required_context) - situation.conditions
    if procedure.subject is not None and "subject-selected" not in situation.conditions:
        missing.add("subject-selected")
    if missing:
        raise ValidationError(
            f"Missing procedure context for {procedure.id}: {', '.join(sorted(missing))}"
        )
    unknown = set(situation.modifiers) - task.allowed_modifiers
    if unknown:
        raise ValidationError(
            f"Modifier does not apply to {procedure.id}: {', '.join(sorted(unknown))}"
        )
    modifiers = situation.situational + tuple(
        Modifier(value, reason, profile_id, "basic-set", ModifierKind.SITUATIONAL)
        for reason, value in situation.modifiers.items()
    )
    target = _target(procedure, performer)
    if target < 1:
        raise ValidationError(f"Effective skill must be positive: {procedure.id}")
    check: CheckTrace | None = None
    contest: QuickContestTrace | None = None
    rounds: RegularContestTrace | None = None
    if task.resolution is Resolution.SUCCESS_ROLL:
        check = success_roll(profile_id, target, modifiers, rng=rng)
        margin = check.margin
        won = check.outcome.succeeded
    else:
        if situation.resistance is None or situation.resistance < 1:
            raise ValidationError(f"Contest requires positive resistance: {procedure.id}")
        actor = Contestant("actor", target, modifiers)
        subject = Contestant("subject", situation.resistance)
        if task.resolution is Resolution.QUICK_CONTEST:
            contest = quick_contest(profile_id, actor, subject, rng=rng)
            margin = contest.victory_margin if contest.winner == "actor" else -1
            won = contest.winner == "actor"
        else:
            rounds = regular_contest(profile_id, actor, subject, rng=rng)
            margin = 0 if rounds.winner == "actor" else -1
            won = rounds.winner == "actor"
    return ProcedureResult(
        procedure.id,
        procedure.reference,
        procedure.dispatch,
        task.effect,
        task.policy,
        task.unit,
        task.units(margin) if won else 0,
        check,
        contest,
        rounds,
    )


def replay(result: ProcedureResult) -> ProcedureResult:
    """Re-score the recorded dice without changing the service handoff."""
    if result.check is not None:
        return replace(result, check=replay_success(result.check))
    if result.contest is not None:
        return replace(result, contest=replay_quick_contest(result.contest))
    if result.rounds is not None:
        return replace(result, rounds=replay_regular_contest(result.rounds))
    raise ValidationError(f"Procedure result carries no check trace: {result.procedure_id}")
