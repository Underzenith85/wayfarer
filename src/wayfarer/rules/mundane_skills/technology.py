"""Executable technology, science and vehicle skill procedures (#346).

Whole-entry support for the technology/vehicle rows of the Basic Set mundane
skill inventory (#112), indexed against B168-B233 and the B301-B304 skill index.

Every procedure names exactly one authoritative service that already resolves
its effect, so no second engine is introduced here. This module supplies the
trusted target, the typed modifiers, the repeated-attempt policy and the outcome
shape that service consumes; scoring always goes through
:mod:`wayfarer.rules.gurps_checks`, which itself scores through the single
authoritative scorer in :mod:`wayfarer.rules.checks`.

Numeric metadata stays reconstructed source data under the standing
first-printing delta audit owned by #336. Nothing here activates a definition:
``wayfarer.rules.mundane_skills.require_available`` still fails closed for every
row, and each procedure additionally declares the capability rows that must
reach ``verified`` before the skill may be offered in live play.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.rules.conformance import (
    BASELINE_ID,
    CAPABILITIES,
    CoverageStatus,
    require_capabilities,
)
from wayfarer.rules.gurps_checks import RepeatedAttemptPolicy, success_roll
from wayfarer.rules.skill_types import SkillSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from wayfarer.rules.catalog import RuleDefinition

PROFILE: Final = "gurps-basic-set-4e-2004"
OWNER: Final = 346
CHECK_CAPABILITIES: Final = (
    "gurps.check.success",
    "gurps.check.margin",
    "gurps.check.critical",
)
# B169 familiarity: an unfamiliar vehicle, model or piece of equipment of a kind
# you do know is a flat penalty, never a refusal to roll.
FAMILIARITY_PENALTY: Final = -2
# B168 technology level: using a TL-tagged skill at another TL costs one point of
# effective skill per level of difference, in either direction.
TECHNOLOGY_LEVEL_PENALTY: Final = -1
VEHICLE_ACTIVATION: Final = ("gurps.vehicles.movement",)


class Dispatch(StrEnum):
    """The authoritative service that already resolves a procedure's effect."""

    VEHICLE_CONTROL = "simulation.transport:transport-control"
    HAZARD_EXPOSURE = "simulation.hazards:resolve"
    OBJECT_REPAIR = "simulation.object_repairs:record"
    NONCOMBAT_APPROACH = "simulation.noncombat:approach"


class Effect(StrEnum):
    """What a successful attempt produces, in the units the service consumes."""

    CONTROL = "vehicle-control"
    CREW_STATION = "crew-station"
    SEAL = "sealed-suit-integrity"
    REPAIR = "repair-progress"
    FINDING = "task-finding"
    ORDNANCE = "ordnance-placement"


@dataclass(frozen=True, slots=True)
class Procedure:
    """One skill family's specific procedure: modifiers, outcome and dispatch.

    ``units_per_margin`` and ``base_units`` describe the quantity a success hands
    the dispatched service, in ``unit``. Specialties and techniques of a family do
    not repeat the procedure; they resolve to their family's or parent's entry.
    """

    family: str
    page: int
    dispatch: Dispatch
    effect: Effect
    policy: RepeatedAttemptPolicy
    unit: str
    base_units: int = 1
    units_per_margin: int = 0
    unit_cap: int = 0
    familiarity: bool = True
    technology_level: bool = True
    handling: bool = False
    # Capability rows that must be verified before live play may offer the skill.
    # The procedure itself still executes and is tested while they stay partial.
    activation_capabilities: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return "procedure:" + self.family.removeprefix("skill:")

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    def units(self, margin: int) -> int:
        """Quantity delivered on a success; a failure delivers nothing at all."""
        if margin < 0:
            return 0
        produced = self.base_units + self.units_per_margin * margin
        return min(produced, self.unit_cap) if self.unit_cap else produced


def _vehicle(family: str, page: int) -> Procedure:
    """Operator control of a vehicle: skill plus Handling, dispatched to transport.

    Loss of control, skid, collision and occupant injury stay owned by
    :mod:`wayfarer.simulation.transport`; this procedure supplies its target only.
    """
    return Procedure(
        family,
        page,
        Dispatch.VEHICLE_CONTROL,
        Effect.CONTROL,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        "control-roll",
        handling=True,
        activation_capabilities=VEHICLE_ACTIVATION,
    )


def _study(family: str, page: int, *, unit: str = "finding", cap: int = 0) -> Procedure:
    """A bounded information task whose margin decides how much is learned."""
    return Procedure(
        family,
        page,
        Dispatch.NONCOMBAT_APPROACH,
        Effect.FINDING,
        RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER,
        unit,
        units_per_margin=1,
        unit_cap=cap,
    )


_PROCEDURES: Final = (
    # --- Vehicle operation (B180-B223) ---------------------------------------
    _vehicle("skill:boating", 180),
    _vehicle("skill:driving", 188),
    _vehicle("skill:piloting", 214),
    _vehicle("skill:shiphandling", 220),
    _vehicle("skill:submarine", 223),
    # B185 Crewman: a rated station aboard a vessel, rolled when the vessel is in
    # trouble. It never steers, so it carries no Handling.
    Procedure(
        "skill:crewman",
        185,
        Dispatch.VEHICLE_CONTROL,
        Effect.CREW_STATION,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        "station-held",
        activation_capabilities=VEHICLE_ACTIVATION,
    ),
    # B192 Environment Suit: keeping a sealed suit sealed. Failure hands the
    # scheduled exposure back to the authoritative hazard service.
    Procedure(
        "skill:environment-suit",
        192,
        Dispatch.HAZARD_EXPOSURE,
        Effect.SEAL,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        "seal-held",
    ),
    # --- Repair and installation (B189-B209) ---------------------------------
    Procedure(
        "skill:electrician",
        189,
        Dispatch.OBJECT_REPAIR,
        Effect.REPAIR,
        RepeatedAttemptPolicy.RETRY_UNTIL_SUCCESS,
        "restored-hp",
        units_per_margin=1,
    ),
    # --- Ordnance and emplacement (B194-B226) --------------------------------
    Procedure(
        "skill:explosives",
        194,
        Dispatch.HAZARD_EXPOSURE,
        Effect.ORDNANCE,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        "charge-placed",
    ),
    Procedure(
        "skill:traps",
        226,
        Dispatch.HAZARD_EXPOSURE,
        Effect.ORDNANCE,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        "trap-placed",
    ),
    # --- Information tasks (B176-B217) ---------------------------------------
    _study("skill:architecture", 176),
    _study("skill:astronomy", 179),
    _study("skill:cartography", 183, unit="mapped-detail"),
    _study("skill:chemistry", 183),
    _study("skill:computer-operation", 184, unit="record-retrieved"),
    _study("skill:computer-programming", 184, unit="program-feature"),
    _study("skill:criminology", 186, unit="clue"),
    _study("skill:cryptography", 186, unit="message-recovered", cap=1),
    _study("skill:forensics", 196, unit="clue"),
    _study("skill:forward-observer", 196, unit="target-designation", cap=1),
    _study("skill:freight-handling", 197, unit="load-secured", cap=1),
    _study("skill:intelligence-analysis", 201),
    _study("skill:mathematics", 207),
    _study("skill:metallurgy", 209),
    _study("skill:physics", 213),
    _study("skill:research", 217),
)

PROCEDURES: Final = MappingProxyType({entry.family: entry for entry in _PROCEDURES})


@dataclass(frozen=True, slots=True)
class Operator:
    """Trusted server description of the character attempting a procedure.

    ``level`` is the character's effective skill for ``skill_id`` as the character
    service already computed it, never a client claim. ``parent_level`` is the
    level of a technique's parent skill and is required for technique rows.
    """

    skill_id: str
    level: int
    technology_level: int
    trained: frozenset[str] = frozenset()
    parent_level: int | None = None


@dataclass(frozen=True, slots=True)
class Task:
    """The authoritative situation the attempt happens in."""

    technology_level: int
    familiar: bool = True
    handling: int = 0
    situational: tuple[Modifier, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcedureResult:
    """One executed attempt, ready for the service named by ``dispatch``."""

    procedure_id: str
    skill_id: str
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


def procedure(spec: SkillSpec, skill_id: str) -> Procedure | None:
    """Resolve the procedure covering one row, following specialty and technique.

    A specialty resolves to its family's procedure and a technique to its parent's;
    neither restates the procedure, so a family can never drift from its children.
    """
    if spec.technique is not None:
        return PROCEDURES.get(spec.technique.parent)
    if spec.specialty is not None:
        return PROCEDURES.get("skill:" + spec.specialty.family)
    return PROCEDURES.get(skill_id)


def covers(identifier: str, family: str | None, parent: str | None) -> bool:
    """Whether a source row is covered, reading its recorded structure only.

    ``identifier`` and ``parent`` are full ``skill:`` identifiers; ``family`` is a
    specialty's bare family name as the source record writes it. Absent metadata is
    never inferred: a row with neither resolves against its own identifier.
    """
    if parent is not None:
        return parent in PROCEDURES
    if family is not None:
        return "skill:" + family in PROCEDURES
    return identifier in PROCEDURES


def implemented_rows() -> frozenset[str]:
    """Family identifiers this module implements; specialties resolve through them."""
    return frozenset(PROCEDURES)


def _modifier(value: int, reason: str, kind: ModifierKind) -> Modifier:
    return Modifier(value, reason, PROFILE, BASELINE_ID, kind)


def technology_level_modifier(operator: Operator, task: Task) -> Modifier | None:
    """B168: one point of effective skill per level of TL difference, either way."""
    difference = abs(operator.technology_level - task.technology_level)
    if difference == 0:
        return None
    return _modifier(
        TECHNOLOGY_LEVEL_PENALTY * difference,
        "technology-level-difference",
        ModifierKind.SITUATIONAL,
    )


def technique_target(spec: SkillSpec, operator: Operator) -> int:
    """B230: a technique starts at its parent's default and is capped above it."""
    technique = spec.technique
    if technique is None:
        raise ValidationError(f"Not a technique: {operator.skill_id}")
    if operator.parent_level is None:
        raise ValidationError(f"Technique requires its parent skill level: {operator.skill_id}")
    floor = operator.parent_level + technique.default_modifier
    ceiling = operator.parent_level + technique.maximum_modifier
    if ceiling < floor:
        raise ValidationError(f"Technique cap is below its default: {operator.skill_id}")
    if not floor <= operator.level <= ceiling:
        raise ValidationError(
            f"Technique level outside its parent-specific range: {operator.skill_id}"
        )
    return operator.level


def _require_prerequisites(spec: SkillSpec, operator: Operator) -> None:
    missing = sorted(p.target for p in spec.prerequisites if p.target not in operator.trained)
    if missing:
        raise ValidationError(
            f"Untrained prerequisite for {operator.skill_id}: {', '.join(missing)}"
        )


def activation_blockers(entry: Procedure) -> tuple[str, ...]:
    """Capability rows this procedure needs before live play may offer the skill."""
    return tuple(
        identifier
        for identifier in entry.activation_capabilities
        if CAPABILITIES[identifier].status is not CoverageStatus.VERIFIED
    )


def attempt(
    definition: RuleDefinition,
    operator: Operator,
    task: Task,
    *,
    rng: RandomSource,
    profile_id: str = PROFILE,
) -> ProcedureResult:
    """Execute one attempt at the procedure covering ``definition``.

    Fails closed on an unknown or uncovered row, an operator attempting somebody
    else's skill, an untrained prerequisite, a technique outside its
    parent-specific range, and any check capability the profile has not verified.
    """
    spec = definition.skill
    if spec is None:
        raise ValidationError(f"Row carries no mechanics: {definition.id}")
    entry = procedure(spec, definition.id)
    if entry is None:
        raise ValidationError(f"No implemented procedure for {definition.id}")
    if operator.skill_id != definition.id:
        raise ValidationError(f"Operator skill {operator.skill_id} does not match {definition.id}")
    require_capabilities(profile_id, CHECK_CAPABILITIES)
    _require_prerequisites(spec, operator)
    base = technique_target(spec, operator) if spec.technique else operator.level
    if base < 1:
        raise ValidationError(f"Effective skill must be positive: {definition.id}")

    modifiers: list[Modifier] = []
    if entry.technology_level:
        recorded = technology_level_modifier(operator, task)
        if recorded is not None:
            modifiers.append(recorded)
    if entry.familiarity and not task.familiar:
        modifiers.append(
            _modifier(FAMILIARITY_PENALTY, "unfamiliar-equipment", ModifierKind.EQUIPMENT)
        )
    if entry.handling and task.handling:
        modifiers.append(_modifier(task.handling, "vehicle-handling", ModifierKind.EQUIPMENT))
    elif not entry.handling and task.handling:
        raise ValidationError(f"Handling does not apply to {entry.id}")
    modifiers.extend(task.situational)

    check = success_roll(profile_id, base, tuple(modifiers), rng=rng)
    return ProcedureResult(
        entry.id,
        definition.id,
        spec.reference,
        entry.dispatch,
        entry.effect,
        entry.policy,
        check,
        entry.units(check.margin) if check.outcome.succeeded else 0,
        entry.unit,
        hazard=entry.policy is RepeatedAttemptPolicy.HAZARDOUS_FAILURE
        and not check.outcome.succeeded,
        activation_blockers=activation_blockers(entry),
    )


def replay(definition: RuleDefinition, result: ProcedureResult) -> ProcedureResult:
    """Re-score a recorded attempt without rolling; receipts must be reproducible."""
    from wayfarer.rules.gurps_checks import replay_success

    spec = definition.skill
    if spec is None or definition.id != result.skill_id:
        raise ValidationError(f"Recorded attempt does not match {definition.id}")
    entry = procedure(spec, definition.id)
    if entry is None or entry.id != result.procedure_id:
        raise ValidationError(f"Recorded procedure is no longer covered: {result.procedure_id}")
    check = replay_success(result.check)
    if check != result.check:
        raise ValidationError(f"Replay diverged from the recorded receipt: {result.procedure_id}")
    return ProcedureResult(
        entry.id,
        definition.id,
        spec.reference,
        entry.dispatch,
        entry.effect,
        entry.policy,
        check,
        entry.units(check.margin) if check.outcome.succeeded else 0,
        entry.unit,
        hazard=entry.policy is RepeatedAttemptPolicy.HAZARDOUS_FAILURE
        and not check.outcome.succeeded,
        activation_blockers=activation_blockers(entry),
    )
