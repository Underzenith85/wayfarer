"""Attempting a technology procedure, and replaying a recorded attempt."""

from __future__ import annotations

from dataclasses import dataclass

from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.engine.rules.conformance import BASELINE_ID, require_capabilities
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy, replay_success, success_roll
from wayfarer.engine.rules.skills.mundane.technology.inventory import (
    CHECK_CAPABILITIES,
    FAMILIARITY_PENALTY,
    PROCEDURES,
    PROFILE,
    TECHNOLOGY_LEVEL_PENALTY,
    Dispatch,
    Effect,
    TechnologyProcedure,
)
from wayfarer.engine.rules.types.skill import PrerequisiteKind, SkillPrerequisite
from wayfarer.errors import ValidationError


@dataclass(frozen=True, slots=True)
class Operator:
    """Trusted server description of the character attempting a procedure.

    ``level`` is the character's effective skill as the character service already
    computed it, never a client claim. ``parent_level`` is the level of a
    technique's parent skill and is required for a technique row; where that
    parent is a family, it is the level of the concrete specialty the character
    actually holds, because that is what B230 measures the technique against.
    """

    skill_id: str
    level: int
    technology_level: int
    trained: frozenset[str] = frozenset()
    parent_level: int | None = None
    purchased_definitions: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Situation:
    """The authoritative situation the attempt happens in."""

    technology_level: int
    familiar: bool = True
    handling: int = 0
    situational: tuple[Modifier, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcedureResult:
    """One executed attempt, ready for the service named by ``dispatch``."""

    procedure_id: str
    reference: str
    dispatch: Dispatch
    effect: Effect
    policy: RepeatedAttemptPolicy
    check: CheckTrace
    units: int
    unit: str
    hazard: bool
    activation_blockers: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return self.check.outcome.succeeded


def require_task(profile_id: str, skill_id: str) -> TechnologyProcedure:
    """Fail closed before dice when a row is a family, unbound or off-profile."""
    entry = PROCEDURES.get(skill_id)
    if entry is None:
        raise ValidationError(f"Skill is outside the technology procedures: {skill_id}")
    if profile_id != PROFILE:
        raise ValidationError(f"Technology skill requires the exact Basic Set profile: {skill_id}")
    if entry.specialties:
        raise ValidationError(
            f"Technology skill family requires a concrete specialty: {skill_id}: "
            + ", ".join(entry.specialties)
        )
    if not entry.dispatchable:
        raise ValidationError(
            f"Technology skill procedure is unsupported: {skill_id}: "
            + ", ".join(
                f"{blocker} (" + ", ".join(f"#{issue}" for issue in owners) + ")"
                for blocker, owners in entry.transferred.items()
            )
        )
    return entry


def _modifier(value: int, reason: str, kind: ModifierKind) -> Modifier:
    return Modifier(value, reason, PROFILE, BASELINE_ID, kind)


def technology_level_modifier(operator: Operator, situation: Situation) -> Modifier | None:
    """B168: one point of effective skill per level of TL difference, either way."""
    difference = abs(operator.technology_level - situation.technology_level)
    if difference == 0:
        return None
    return _modifier(
        TECHNOLOGY_LEVEL_PENALTY * difference,
        "technology-level-difference",
        ModifierKind.SITUATIONAL,
    )


def technique_target(entry: TechnologyProcedure, operator: Operator) -> int:
    """B230: a technique starts at its parent's default and is capped above it."""
    technique = entry.technique
    assert technique is not None
    if operator.parent_level is None:
        raise ValidationError(f"Technique requires its parent skill level: {entry.id}")
    floor = operator.parent_level + technique.default_modifier
    ceiling = operator.parent_level + technique.maximum_modifier
    if not floor <= operator.level <= ceiling:
        raise ValidationError(f"Technique level outside its parent-specific range: {entry.id}")
    return operator.level


def _modifiers(
    entry: TechnologyProcedure, operator: Operator, situation: Situation
) -> tuple[Modifier, ...]:
    assert entry.task is not None
    modifiers: list[Modifier] = []
    recorded = technology_level_modifier(operator, situation)
    if recorded is not None:
        modifiers.append(recorded)
    if not situation.familiar:
        modifiers.append(
            _modifier(FAMILIARITY_PENALTY, "unfamiliar-equipment", ModifierKind.EQUIPMENT)
        )
    if situation.handling:
        if not entry.task.handling:
            raise ValidationError(f"Handling does not apply to {entry.id}")
        modifiers.append(_modifier(situation.handling, "vehicle-handling", ModifierKind.EQUIPMENT))
    modifiers.extend(situation.situational)
    return tuple(modifiers)


def _result(entry: TechnologyProcedure, check: CheckTrace) -> ProcedureResult:
    assert entry.task is not None
    return ProcedureResult(
        entry.id,
        entry.reference,
        entry.task.dispatch,
        entry.task.effect,
        entry.task.policy,
        check,
        entry.task.units(check.margin) if check.outcome.succeeded else 0,
        entry.task.unit,
        hazard=entry.task.policy is RepeatedAttemptPolicy.HAZARDOUS_FAILURE
        and not check.outcome.succeeded,
        activation_blockers=entry.activation_blockers,
    )


def attempt(
    operator: Operator,
    situation: Situation,
    *,
    rng: RandomSource,
    profile_id: str = PROFILE,
) -> ProcedureResult:
    """Execute one attempt at the procedure bound to the operator's skill.

    Fails closed on an unbound or family row, an untrained prerequisite, a
    technique outside its parent-specific range, a steering modifier on a
    procedure that does not steer, and any check capability the profile has not
    verified.
    """
    entry = require_task(profile_id, operator.skill_id)
    require_capabilities(profile_id, CHECK_CAPABILITIES)
    base = technique_target(entry, operator) if entry.technique else operator.level
    if base < 1:
        raise ValidationError(f"Effective skill must be positive: {entry.id}")

    def acquisition_satisfied(prerequisite: SkillPrerequisite) -> bool:
        if (
            prerequisite.minimum_technology_level is not None
            and operator.technology_level < prerequisite.minimum_technology_level
        ):
            return True
        return (
            prerequisite.target in operator.trained
            if prerequisite.kind is PrerequisiteKind.TRAINED_SKILL
            else prerequisite.target in operator.purchased_definitions
            if prerequisite.kind is PrerequisiteKind.PURCHASED_DEFINITION
            else prerequisite.target in operator.capabilities
        )

    missing = [
        prerequisite.target
        for prerequisite in entry.prerequisites
        if not acquisition_satisfied(prerequisite)
    ]
    missing.extend(
        "/".join(prerequisite.target for prerequisite in group.alternatives)
        for group in entry.prerequisite_groups
        if not any(acquisition_satisfied(prerequisite) for prerequisite in group.alternatives)
    )
    if missing:
        raise ValidationError(
            f"Untrained prerequisite for {entry.id}: {', '.join(sorted(missing))}"
        )
    return _result(
        entry, success_roll(profile_id, base, _modifiers(entry, operator, situation), rng=rng)
    )


def replay(result: ProcedureResult) -> ProcedureResult:
    """Re-score a recorded attempt without rolling; receipts must be reproducible."""
    entry = PROCEDURES.get(result.procedure_id)
    if entry is None or not entry.dispatchable:
        raise ValidationError(f"Recorded procedure is no longer bound: {result.procedure_id}")
    check = replay_success(result.check)
    if check != result.check:
        raise ValidationError(f"Replay diverged from the recorded receipt: {result.procedure_id}")
    return _result(entry, check)
