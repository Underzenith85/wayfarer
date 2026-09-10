"""Executable technology, science and vehicle skill procedures (#346).

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B168-233 skill chapter with the B301-304 index. The frozen
first-printing/2007-01-26 errata delta stays #191's blocker on every row, so a
bound procedure is reported as implemented and is still not certified.

Recording a skill never makes it playable. A row is implemented only when this
module binds it to a service that already resolves it -- `simulation/transport`
for vehicle control, `simulation/hazards` for sealed suits and ordnance,
`simulation/object_repairs` for repair work, and `simulation/noncombat` for
information tasks -- and scoring always goes through `rules/gurps_checks`, so no
second engine exists here. Every other listed row keeps its recorded blockers and
names the concrete open child that owns them: #356 the discipline-keyed specialty
families, #338 the Photography parent of Motion-Picture Camera, #336 conditional
defaults, alternative prerequisites and the frozen-source context.

Two modifiers belong to the procedure itself: the B168 technology-level
difference and the B169 familiarity penalty. Handling reaches only a procedure
that actually steers. A dispatched vehicle row additionally names
`gurps.vehicles.movement`, which #358 must verify before live play may offer it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.rules.conformance import (
    BASELINE_ID,
    CAPABILITIES,
    CoverageStatus,
    require_capabilities,
)
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.gurps_checks import RepeatedAttemptPolicy, replay_success, success_roll
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import (
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
    Technique,
)

PROFILE: Final = "gurps-basic-set-4e-2004"
OWNER: Final = 346
SPECIALTY_OWNER: Final = 356
ARTS_OWNER: Final = 338
CONTEXT_OWNER: Final = 336
CAPABILITY_OWNER: Final = 358

RUNTIME_PROCEDURE: Final = "runtime-procedure"
SPECIALTY_EXPANSION: Final = "specialty-expansion"
TECHNIQUE_EXPANSION: Final = "technique-expansion"
CONDITIONAL_DEFAULTS: Final = "conditional-or-skill-defaults"
PREREQUISITE_PROCEDURE: Final = "prerequisite-procedure"
TECHNOLOGY_LEVEL: Final = "technology-level-context"

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
# Contextual blockers this issue does not close; the source review owns them.
CONTEXT: Final = MappingProxyType(
    {
        CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,),
        PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,),
    }
)


class Dispatch(StrEnum):
    """The authoritative service that already resolves a procedure's effect."""

    VEHICLE_CONTROL = "transport.vehicle-control"
    HAZARD_EXPOSURE = "hazard.exposure"
    OBJECT_REPAIR = "object.repair"
    NONCOMBAT_APPROACH = "noncombat.approach"


class Effect(StrEnum):
    """What a successful attempt produces, in the units the service consumes."""

    CONTROL = "vehicle-control"
    CREW_STATION = "crew-station"
    SEAL = "sealed-suit-integrity"
    REPAIR = "repair-progress"
    FINDING = "task-finding"
    ORDNANCE = "ordnance-placement"


@dataclass(frozen=True, slots=True)
class TaskClass:
    """The exact task one skill governs; anything else is another skill.

    These are task shapes, not table values: which service resolves the outcome,
    what a success hands it and in what unit, whether repeating the attempt is
    allowed, and whether the roll is steered. A skill that accepted any task
    would be generic target calculation, not whole-entry support.
    """

    dispatch: Dispatch
    effect: Effect
    policy: RepeatedAttemptPolicy
    unit: str
    base_units: int = 1
    units_per_margin: int = 0
    unit_cap: int = 0
    handling: bool = False
    activation: tuple[str, ...] = ()

    def units(self, margin: int) -> int:
        """Quantity delivered on a success; a failure delivers nothing at all."""
        if margin < 0:
            return 0
        produced = self.base_units + self.units_per_margin * margin
        return min(produced, self.unit_cap) if self.unit_cap else produced


CONTROL: Final = TaskClass(
    Dispatch.VEHICLE_CONTROL,
    Effect.CONTROL,
    RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
    "control-roll",
    handling=True,
    activation=VEHICLE_ACTIVATION,
)
# B185 Crewman holds a rated station; it never steers, so it carries no Handling.
STATION: Final = TaskClass(
    Dispatch.VEHICLE_CONTROL,
    Effect.CREW_STATION,
    RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
    "station-held",
    activation=VEHICLE_ACTIVATION,
)
SEAL: Final = TaskClass(
    Dispatch.HAZARD_EXPOSURE,
    Effect.SEAL,
    RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
    "seal-held",
)
REPAIR: Final = TaskClass(
    Dispatch.OBJECT_REPAIR,
    Effect.REPAIR,
    RepeatedAttemptPolicy.RETRY_UNTIL_SUCCESS,
    "restored-hp",
    units_per_margin=1,
)


def _ordnance(unit: str) -> TaskClass:
    return TaskClass(
        Dispatch.HAZARD_EXPOSURE,
        Effect.ORDNANCE,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        unit,
    )


def _study(unit: str = "finding", cap: int = 0) -> TaskClass:
    """A bounded information task whose margin decides how much is learned."""
    return TaskClass(
        Dispatch.NONCOMBAT_APPROACH,
        Effect.FINDING,
        RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER,
        unit,
        units_per_margin=1,
        unit_cap=cap,
    )


@dataclass(frozen=True, slots=True)
class TechnologyProcedure:
    """One accounted-for technology row and the dispatch it does or does not have."""

    id: str
    name: str
    page: int
    attribute: A
    difficulty: D
    defaults: tuple[SkillDefault, ...] = ()
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    specialty: Specialty | None = None
    technique: Technique | None = None
    task: TaskClass | None = None
    # A family row is never dispatched; it is completed by its concrete specialties.
    specialties: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    # Blockers this issue does not close, each mapped to the concrete open child
    # that owns it. A transferred blocker without an owner is a coverage failure.
    transferred: Mapping[str, tuple[int, ...]] = field(default_factory=dict)

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(self.transferred)

    @property
    def owners(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(i for owners in self.transferred.values() for i in owners))

    @property
    def implemented(self) -> bool:
        """A bound dispatch executes; recording a procedure never implements it."""
        return RUNTIME_PROCEDURE in self.resolved

    @property
    def dispatchable(self) -> bool:
        return self.implemented and self.task is not None and not self.specialties

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    @property
    def dispatch(self) -> str | None:
        return self.task.dispatch.value if self.dispatchable and self.task else None

    @property
    def activation_blockers(self) -> tuple[str, ...]:
        """Capability rows this dispatch needs before live play may offer the skill."""
        if self.task is None:
            return ()
        return tuple(
            identifier
            for identifier in self.task.activation
            if CAPABILITIES[identifier].status is not CoverageStatus.VERIFIED
        )

    def spec(self) -> SkillSpec:
        return SkillSpec(
            self.attribute,
            self.difficulty,
            self.reference,
            self.defaults,
            self.prerequisites,
            self.specialty,
            self.technique,
        )

    def definition(self) -> RuleDefinition:
        if not self.dispatchable:
            raise ValidationError(f"Technology skill has no bound dispatch: {self.id}")
        assert self.task is not None
        return RuleDefinition(
            self.id,
            DefinitionKind.SKILL,
            self.name,
            source(PROFILE).id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", self.task.dispatch.value),
            skill=self.spec(),
        )


def _attribute(target: A, modifier: int) -> tuple[SkillDefault, ...]:
    return (SkillDefault(target, modifier),)


def _skills(*pairs: tuple[str, int]) -> tuple[SkillDefault, ...]:
    return tuple(SkillDefault(f"skill:{target}", modifier) for target, modifier in pairs)


# --- Vehicle families and their concrete specialties --------------------------

VEHICLE_FAMILIES: Final = {
    "boating": (
        "Boating",
        180,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5), SkillDefault(A.IQ, -5)),
        (
            ("large-powerboat", "Large Powerboat"),
            ("motorboat", "Motorboat"),
            ("sailboat", "Sailboat"),
            ("unpowered", "Unpowered"),
        ),
        {CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    "driving": (
        "Driving",
        188,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5), SkillDefault(A.IQ, -5)),
        (
            ("automobile", "Automobile"),
            ("construction-equipment", "Construction Equipment"),
            ("halftrack", "Halftrack"),
            ("heavy-wheeled", "Heavy Wheeled"),
            ("hovercraft", "Hovercraft"),
            ("locomotive", "Locomotive"),
            ("mecha", "Mecha"),
            ("motorcycle", "Motorcycle"),
            ("tracked", "Tracked"),
        ),
        {CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    "piloting": (
        "Piloting",
        214,
        A.DX,
        D.AVERAGE,
        _attribute(A.IQ, -6),
        (
            ("aerospace", "Aerospace"),
            ("airship", "Airship"),
            ("autogyro", "Autogyro"),
            ("contragravity", "Contragravity"),
            ("flight-pack", "Flight Pack"),
            ("glider", "Glider"),
            ("heavy-airplane", "Heavy Airplane"),
            ("high-performance-airplane", "High-Performance Airplane"),
            ("high-performance-spacecraft", "High-Performance Spacecraft"),
            ("light-airplane", "Light Airplane"),
            ("lighter-than-air", "Lighter-Than-Air"),
            ("low-performance-spacecraft", "Low-Performance Spacecraft"),
            ("ultralight", "Ultralight"),
            ("vertol", "Vertol"),
        ),
        {},
    ),
    "shiphandling": (
        "Shiphandling",
        220,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        (
            ("airship", "Airship"),
            ("ship", "Ship"),
            ("starship", "Starship"),
            ("submarine", "Submarine"),
        ),
        {CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,), PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,)},
    ),
    "submarine": (
        "Submarine",
        223,
        A.DX,
        D.AVERAGE,
        _attribute(A.IQ, -6),
        (
            ("free-flooding", "Free-Flooding Sub"),
            ("large", "Large Sub"),
            ("mini", "Mini-Sub"),
        ),
        {},
    ),
}
EXPLOSIVES_SPECIALTIES: Final = (
    ("demolition", "Demolition", "charge-placed"),
    ("explosive-ordnance-disposal", "Explosive Ordnance Disposal", "device-disarmed"),
    ("fireworks", "Fireworks", "display-fired"),
    ("nuclear-ordnance-disposal", "Nuclear Ordnance Disposal", "device-disarmed"),
    ("underwater-demolition", "Underwater Demolition", "charge-placed"),
)


def _vehicle_rows() -> tuple[TechnologyProcedure, ...]:
    """One family row plus its concrete specialties, sharing the family's mechanics."""
    rows: list[TechnologyProcedure] = []
    for family, (
        title,
        page,
        attribute,
        difficulty,
        defaults,
        members,
        context,
    ) in VEHICLE_FAMILIES.items():
        specialties = tuple(f"skill:{family}-{name}" for name, _ in members)
        rows.append(
            TechnologyProcedure(
                f"skill:{family}",
                title,
                page,
                attribute,
                difficulty,
                defaults,
                specialties=specialties,
                resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
                transferred=dict(context),
            )
        )
        rows.extend(
            TechnologyProcedure(
                f"skill:{family}-{name}",
                f"{title} ({label})",
                page,
                attribute,
                difficulty,
                defaults,
                specialty=Specialty(family, name),
                task=CONTROL,
                resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
                transferred=dict(context),
            )
            for name, label in members
        )
    return tuple(rows)


def _crew(name: str, title: str) -> TechnologyProcedure:
    """One concrete Crewman specialty (B185); specialties never infer each other."""
    return TechnologyProcedure(
        f"skill:{name}",
        title,
        185,
        A.IQ,
        D.EASY,
        _attribute(A.IQ, -4),
        specialty=Specialty("crewman", name),
        task=STATION,
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
    )


def _suit(name: str, title: str, defaults: tuple[SkillDefault, ...]) -> TechnologyProcedure:
    """One concrete Environment Suit specialty (B192) with its recorded cross-defaults."""
    return TechnologyProcedure(
        f"skill:{name}",
        title,
        192,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5), *defaults),
        specialty=Specialty("environment-suit", name),
        task=SEAL,
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
    )


def _maths(name: str, title: str, *pairs: tuple[str, int]) -> TechnologyProcedure:
    """One concrete Mathematics specialty (B207) with its recorded cross-defaults."""
    context = {CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)} if name in ("applied", "surveying") else {}
    return TechnologyProcedure(
        f"skill:mathematics-{name}",
        f"Mathematics ({title})",
        207,
        A.IQ,
        D.HARD,
        (SkillDefault(A.IQ, -6), *_skills(*pairs)),
        specialty=Specialty("mathematics", name),
        task=_study(),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred=context,
    )


def _task(
    name: str,
    title: str,
    page: int,
    attribute: A,
    difficulty: D,
    defaults: tuple[SkillDefault, ...],
    task: TaskClass,
    *,
    prerequisites: tuple[str, ...] = (),
    context: Mapping[str, tuple[int, ...]] | None = None,
) -> TechnologyProcedure:
    """A standalone row this issue binds to its own dispatch."""
    return TechnologyProcedure(
        f"skill:{name}",
        title,
        page,
        attribute,
        difficulty,
        defaults,
        tuple(SkillPrerequisite(f"skill:{target}") for target in prerequisites),
        task=task,
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred=dict(context or {}),
    )


def _transferred(
    name: str,
    title: str,
    page: int,
    attribute: A,
    difficulty: D,
    defaults: tuple[SkillDefault, ...],
    owners: Mapping[str, tuple[int, ...]],
) -> TechnologyProcedure:
    """A listed row this issue does not implement; every blocker names its owner."""
    return TechnologyProcedure(
        f"skill:{name}", title, page, attribute, difficulty, defaults, transferred=dict(owners)
    )


_SPECIALTY_TRANSFER: Final = {
    RUNTIME_PROCEDURE: (SPECIALTY_OWNER,),
    SPECIALTY_EXPANSION: (SPECIALTY_OWNER,),
    TECHNOLOGY_LEVEL: (SPECIALTY_OWNER,),
    CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,),
}

_ROWS: Final = (
    *_vehicle_rows(),
    # B185 Crewman: the family row and the four concrete stations it expands into.
    TechnologyProcedure(
        "skill:crewman",
        "Crewman",
        185,
        A.IQ,
        D.EASY,
        _attribute(A.IQ, -4),
        specialties=("skill:airshipman", "skill:seamanship", "skill:spacer", "skill:submariner"),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    _crew("airshipman", "Airshipman"),
    _crew("seamanship", "Seamanship"),
    _crew("spacer", "Spacer"),
    _crew("submariner", "Submariner"),
    # B192 Environment Suit: the family row and its four concrete suits.
    TechnologyProcedure(
        "skill:environment-suit",
        "Environment Suit",
        192,
        A.DX,
        D.AVERAGE,
        _attribute(A.DX, -5),
        specialties=(
            "skill:battlesuit",
            "skill:diving-suit",
            "skill:nbc-suit",
            "skill:vacc-suit",
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _suit(
        "battlesuit",
        "Battlesuit",
        _skills(("diving-suit", -4), ("nbc-suit", -2), ("vacc-suit", -2)),
    ),
    _suit(
        "diving-suit",
        "Diving Suit",
        _skills(("battlesuit", -4), ("nbc-suit", -4), ("vacc-suit", -4), ("scuba", -2)),
    ),
    _suit(
        "nbc-suit", "NBC Suit", _skills(("battlesuit", -2), ("diving-suit", -4), ("vacc-suit", -2))
    ),
    _suit(
        "vacc-suit", "Vacc Suit", _skills(("battlesuit", -2), ("diving-suit", -4), ("nbc-suit", -2))
    ),
    # B194 Explosives: the family row and its five concrete specialties.
    TechnologyProcedure(
        "skill:explosives",
        "Explosives",
        194,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        specialties=tuple(f"skill:explosives-{name}" for name, _, _ in EXPLOSIVES_SPECIALTIES),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    *(
        TechnologyProcedure(
            f"skill:explosives-{name}",
            f"Explosives ({label})",
            194,
            A.IQ,
            D.AVERAGE,
            _attribute(A.IQ, -5),
            specialty=Specialty("explosives", name),
            task=_ordnance(unit),
            resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
            transferred={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
        )
        for name, label, unit in EXPLOSIVES_SPECIALTIES
    ),
    # B207 Mathematics: the family row and its six recorded specialties.
    TechnologyProcedure(
        "skill:mathematics",
        "Mathematics",
        207,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        specialties=(
            "skill:mathematics-applied",
            "skill:mathematics-computer-science",
            "skill:mathematics-cryptology",
            "skill:mathematics-pure",
            "skill:mathematics-statistics",
            "skill:mathematics-surveying",
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _maths(
        "applied",
        "Applied",
        ("mathematics-computer-science", -5),
        ("mathematics-cryptology", -5),
        ("mathematics-pure", -5),
        ("mathematics-statistics", -5),
        ("mathematics-surveying", -5),
        ("physics", -5),
    ),
    _maths(
        "computer-science",
        "Computer Science",
        ("mathematics-applied", -5),
        ("mathematics-cryptology", -5),
        ("mathematics-pure", -5),
        ("mathematics-statistics", -5),
        ("mathematics-surveying", -5),
        ("computer-programming", -5),
    ),
    _maths(
        "cryptology",
        "Cryptology",
        ("mathematics-applied", -5),
        ("mathematics-computer-science", -5),
        ("mathematics-pure", -5),
        ("mathematics-statistics", -5),
        ("mathematics-surveying", -5),
        ("cryptography", -5),
    ),
    _maths(
        "pure",
        "Pure",
        ("mathematics-applied", -5),
        ("mathematics-computer-science", -5),
        ("mathematics-cryptology", -5),
        ("mathematics-statistics", -5),
        ("mathematics-surveying", -5),
    ),
    _maths(
        "statistics",
        "Statistics",
        ("mathematics-applied", -5),
        ("mathematics-computer-science", -5),
        ("mathematics-cryptology", -5),
        ("mathematics-pure", -5),
        ("mathematics-surveying", -5),
    ),
    _maths(
        "surveying",
        "Surveying",
        ("mathematics-applied", -5),
        ("mathematics-computer-science", -5),
        ("mathematics-cryptology", -5),
        ("mathematics-pure", -5),
        ("mathematics-statistics", -5),
        ("cartography", -3),
    ),
    # Repair and installation.
    _task(
        "electrician",
        "Electrician",
        189,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        REPAIR,
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    # Emplacement.
    _task(
        "traps",
        "Traps",
        226,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _ordnance("trap-placed"),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    # Information tasks.
    _task(
        "architecture",
        "Architecture",
        176,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "astronomy",
        "Astronomy",
        179,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study(),
        prerequisites=("mathematics-applied",),
    ),
    _task(
        "cartography",
        "Cartography",
        183,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study("mapped-detail"),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "chemistry",
        "Chemistry",
        183,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "computer-operation",
        "Computer Operation",
        184,
        A.IQ,
        D.EASY,
        _attribute(A.IQ, -4),
        _study("record-retrieved"),
    ),
    _task(
        "computer-programming",
        "Computer Programming",
        184,
        A.IQ,
        D.HARD,
        (),
        _study("program-feature"),
    ),
    _task(
        "criminology",
        "Criminology",
        186,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study("clue"),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "cryptography",
        "Cryptography",
        186,
        A.IQ,
        D.HARD,
        (),
        _study("message-recovered", 1),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "forensics",
        "Forensics",
        196,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study("clue"),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "forward-observer",
        "Forward Observer",
        196,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study("target-designation", 1),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "freight-handling",
        "Freight Handling",
        197,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study("load-secured", 1),
    ),
    _task(
        "intelligence-analysis",
        "Intelligence Analysis",
        201,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "metallurgy",
        "Metallurgy",
        209,
        A.IQ,
        D.HARD,
        (),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,)},
    ),
    _task(
        "physics",
        "Physics",
        213,
        A.IQ,
        D.VERY_HARD,
        _attribute(A.IQ, -6),
        _study(),
        context={PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,)},
    ),
    _task(
        "research",
        "Research",
        217,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study(),
        context={
            CONDITIONAL_DEFAULTS: (CONTEXT_OWNER,),
            PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,),
        },
    ),
    # B169/B213 optional Physics specialty, bound through its own dispatch.
    TechnologyProcedure(
        "skill:physics-acoustics",
        "Physics (Acoustics)",
        213,
        A.IQ,
        D.HARD,
        specialty=Specialty("physics", "acoustics", "skill:physics"),
        task=_study(),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,)},
    ),
    # B233 techniques: each bounded by its own parent, never by a generic rule.
    # Bought against the concrete Piloting specialty the character flies, so it
    # carries the same control dispatch those specialties do.
    TechnologyProcedure(
        "skill:no-landing-extraction",
        "No-Landing Extraction",
        233,
        A.DX,
        D.HARD,
        technique=Technique("skill:piloting", -5),
        task=CONTROL,
        resolved=(RUNTIME_PROCEDURE, TECHNIQUE_EXPANSION),
    ),
    TechnologyProcedure(
        "skill:set-trap",
        "Set Trap",
        233,
        A.IQ,
        D.AVERAGE,
        technique=Technique("skill:traps", -2),
        task=_ordnance("trap-placed"),
        resolved=(RUNTIME_PROCEDURE, TECHNIQUE_EXPANSION),
    ),
    TechnologyProcedure(
        "skill:work-by-touch",
        "Work by Touch",
        233,
        A.IQ,
        D.HARD,
        technique=Technique("skill:traps", -5),
        task=_ordnance("trap-placed"),
        resolved=(RUNTIME_PROCEDURE, TECHNIQUE_EXPANSION),
    ),
    # Its parent Photography (B213) belongs to the arts and trades group, so the
    # technique cannot dispatch until #338 binds that parent.
    TechnologyProcedure(
        "skill:motion-picture-camera",
        "Motion-Picture Camera",
        233,
        A.IQ,
        D.AVERAGE,
        technique=Technique("skill:photography", -2),
        transferred={
            TECHNIQUE_EXPANSION: (ARTS_OWNER,),
            RUNTIME_PROCEDURE: (ARTS_OWNER,),
        },
    ),
    # Transferred rows: their specialty axis is a discipline, not a vehicle class.
    _transferred("bioengineering", "Bioengineering", 180, A.IQ, D.HARD, (), _SPECIALTY_TRANSFER),
    _transferred(
        "biology", "Biology", 180, A.IQ, D.VERY_HARD, _attribute(A.IQ, -6), _SPECIALTY_TRANSFER
    ),
    _transferred(
        "current-affairs",
        "Current Affairs",
        186,
        A.IQ,
        D.EASY,
        _attribute(A.IQ, -4),
        _SPECIALTY_TRANSFER,
    ),
    _transferred(
        "disguise", "Disguise", 187, A.IQ, D.AVERAGE, _attribute(A.IQ, -5), _SPECIALTY_TRANSFER
    ),
    _transferred(
        "electronics-operation",
        "Electronics Operation",
        189,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _SPECIALTY_TRANSFER,
    ),
    _transferred(
        "electronics-repair",
        "Electronics Repair",
        190,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _SPECIALTY_TRANSFER,
    ),
    _transferred(
        "engineer",
        "Engineer",
        190,
        A.IQ,
        D.HARD,
        (),
        _SPECIALTY_TRANSFER | {PREREQUISITE_PROCEDURE: (CONTEXT_OWNER,)},
    ),
    _transferred(
        "geography", "Geography", 198, A.IQ, D.HARD, _attribute(A.IQ, -6), _SPECIALTY_TRANSFER
    ),
    _transferred(
        "geology", "Geology", 198, A.IQ, D.HARD, _attribute(A.IQ, -6), _SPECIALTY_TRANSFER
    ),
    _transferred(
        "hazardous-materials",
        "Hazardous Materials",
        199,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        {
            RUNTIME_PROCEDURE: (SPECIALTY_OWNER,),
            SPECIALTY_EXPANSION: (SPECIALTY_OWNER,),
            TECHNOLOGY_LEVEL: (SPECIALTY_OWNER,),
        },
    ),
    _transferred(
        "mechanic", "Mechanic", 207, A.IQ, D.AVERAGE, _attribute(A.IQ, -5), _SPECIALTY_TRANSFER
    ),
    _transferred("paleontology", "Paleontology", 212, A.IQ, D.HARD, (), _SPECIALTY_TRANSFER),
)
# Every listed technology row, plus the concrete specialties this issue expands.
PROCEDURES: Final = MappingProxyType({entry.id: entry for entry in _ROWS})


def definitions() -> tuple[RuleDefinition, ...]:
    """Dispatchable technology skills for a new package pin; unbound rows are absent."""
    return tuple(entry.definition() for entry in _ROWS if entry.dispatchable)


# --- Execution ----------------------------------------------------------------


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
    missing = sorted(p.target for p in entry.prerequisites if p.target not in operator.trained)
    if missing:
        raise ValidationError(f"Untrained prerequisite for {entry.id}: {', '.join(missing)}")
    base = technique_target(entry, operator) if entry.technique else operator.level
    if base < 1:
        raise ValidationError(f"Effective skill must be positive: {entry.id}")
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
