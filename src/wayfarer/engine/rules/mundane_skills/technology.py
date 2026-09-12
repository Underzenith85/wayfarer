"""Executable technology, science and vehicle skill procedures (#346).

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B168-233 skill chapter with the B301-304 index. Selected-printing source identity
is tracked separately from remaining mechanics gaps.

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
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, RandomSource
from wayfarer.engine.rules.conformance import (
    BASELINE_ID,
    CAPABILITIES,
    CoverageStatus,
    require_capabilities,
)
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy, replay_success, success_roll
from wayfarer.engine.rules.mundane_skills.source_defaults import (
    recorded_blockers,
    recorded_defaults,
)
from wayfarer.engine.rules.skill_types import ControllingAttribute as A
from wayfarer.engine.rules.skill_types import Difficulty as D
from wayfarer.engine.rules.skill_types import (
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
    Technique,
)
from wayfarer.errors import ValidationError

PROFILE: Final = "gurps-basic-set-4e-2004"
OWNER: Final = 346
SPECIALTY_OWNER: Final = 356
ARTS_OWNER: Final = 338
CONTEXT_OWNER: Final = 336
# #336 split its remaining contextual work into concrete children; conditional
# defaults and alternative prerequisites are owned by #383.
CONDITIONAL_OWNER: Final = 383
CAPABILITY_OWNER: Final = 358
# A family whose specialty is a campaign subject rather than a listed one is
# expanded here but dispatched by #390.
OPEN_SUBJECT_OWNER: Final = 390

RUNTIME_PROCEDURE: Final = "runtime-procedure"
SPECIALTY_EXPANSION: Final = "specialty-expansion"
TECHNIQUE_EXPANSION: Final = "technique-expansion"
CONDITIONAL_DEFAULTS: Final = "conditional-or-skill-defaults"
CONTEXTUAL_DEFAULTS: Final = "contextual-default-procedure"
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
        CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,),
        PREREQUISITE_PROCEDURE: (CONDITIONAL_OWNER,),
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


# A bounded research finding: the shape every science row that simply learns
# something shares. Naming it once keeps those rows from drifting apart.
ANALYSIS: Final = _study()


@dataclass(frozen=True, slots=True)
class UnsupportedScope:
    """A capability a bound row needs that the registry has not verified yet."""

    id: str
    detail: str
    owner_issue: int


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
    prerequisite_groups: tuple[PrerequisiteGroup, ...] = ()
    specialty: Specialty | None = None
    technique: Technique | None = None
    task: TaskClass | None = None
    # A family row is never dispatched; it is completed by its concrete specialties.
    specialties: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    # B180-B198 key a few families to a world, a planet type, a species or a
    # region. That subject is campaign data, so the row records the axis in
    # place of a specialty list and never dispatches on its own.
    open_subject: str | None = None
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
        return (
            self.implemented
            and self.task is not None
            and not self.specialties
            and self.open_subject is None
        )

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
            self.prerequisite_groups,
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
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
                _ship_prerequisites(name) if family == "shiphandling" else (),
                specialty=Specialty(family, name),
                task=CONTROL,
                resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
                transferred=dict(context),
            )
            for name, label in members
        )
    return tuple(rows)


def _ship_prerequisites(name: str) -> tuple[SkillPrerequisite, ...]:
    """B220 crew, leadership, and navigation requirements for each command specialty."""
    targets = {
        "airship": ("airshipman", "leadership", "trained-navigation-air"),
        "ship": ("leadership", "trained-navigation-sea", "seamanship"),
        "starship": ("leadership", "trained-navigation-hyperspace", "spacer"),
        "submarine": ("leadership", "trained-navigation-sea", "submariner"),
    }[name]
    return tuple(
        SkillPrerequisite(
            target if target.startswith("trained-navigation-") else f"skill:{target}",
            kind=(
                PrerequisiteKind.CAPABILITY
                if target.startswith("trained-navigation-")
                else PrerequisiteKind.TRAINED_SKILL
            ),
        )
        for target in targets
    )


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
    context = (
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)} if name in ("applied", "surveying") else {}
    )
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
    acquisition: tuple[SkillPrerequisite, ...] = (),
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
        tuple(SkillPrerequisite(f"skill:{target}") for target in prerequisites) + acquisition,
        task=task,
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred=dict(context or {}),
    )


# --- Discipline-keyed families (#356) -----------------------------------------

# B199 Hazardous Materials keeps a dangerous substance contained rather than
# learning anything, so it resolves through the exposure service the suits use.
CONTAINMENT: Final = TaskClass(
    Dispatch.HAZARD_EXPOSURE,
    Effect.SEAL,
    RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
    "containment-held",
)
# A recall roll answers "what do you already know"; the margin adds detail, but
# a topic only holds so much, so the yield is capped rather than unbounded.
BRIEFING: Final = _study("news-item", cap=3)
DESIGN: Final = _study("design-step")
READOUT: Final = _study("reading")

# B189 and B190 key both electronics rows to the same equipment families. Only
# the repair row adds Computers: B184 Computer Operation is the skill that uses a
# computer, so operating one is never an Electronics Operation specialty.
ELECTRONICS_SPECIALTIES: Final = (
    ("communications", "Communications"),
    ("electronic-warfare", "Electronic Warfare"),
    ("media", "Media"),
    ("medical", "Medical"),
    ("scientific", "Scientific"),
    ("security", "Security"),
    ("sensors", "Sensors"),
    ("sonar", "Sonar"),
    ("surveillance", "Surveillance"),
)
REPAIRABLE_ELECTRONICS: Final = tuple(
    sorted((*ELECTRONICS_SPECIALTIES, ("computers", "Computers")))
)

# Families the source enumerates: family -> (title, page, difficulty, defaults,
# task, specialties, contextual blockers this issue does not close). Every
# specialty shares the family's attribute, difficulty and defaults; a specialty
# that rolled against different numbers would be a different skill.
SCIENCE_FAMILIES: Final = {
    "bioengineering": (
        "Bioengineering",
        180,
        D.HARD,
        (),
        DESIGN,
        (
            ("cloning", "Cloning"),
            ("genetic-engineering", "Genetic Engineering"),
            ("tissue-engineering", "Tissue Engineering"),
        ),
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    "current-affairs": (
        "Current Affairs",
        186,
        D.EASY,
        _attribute(A.IQ, -4),
        BRIEFING,
        (
            ("business", "Business"),
            ("headline-news", "Headline News"),
            ("high-culture", "High Culture"),
            ("people", "People"),
            ("politics", "Politics"),
            ("popular-culture", "Popular Culture"),
            ("science-and-technology", "Science and Technology"),
            ("sports", "Sports"),
        ),
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    "electronics-operation": (
        "Electronics Operation",
        189,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        READOUT,
        ELECTRONICS_SPECIALTIES,
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    "electronics-repair": (
        "Electronics Repair",
        190,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        REPAIR,
        REPAIRABLE_ELECTRONICS,
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    "engineer": (
        "Engineer",
        190,
        D.HARD,
        (),
        DESIGN,
        (
            ("artillery", "Artillery"),
            ("civil", "Civil"),
            ("clockwork", "Clockwork"),
            ("combat", "Combat"),
            ("electrical", "Electrical"),
            ("electronics", "Electronics"),
            ("materials", "Materials"),
            ("mining", "Mining"),
            ("robotics", "Robotics"),
            ("small-arms", "Small Arms"),
        ),
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    "hazardous-materials": (
        "Hazardous Materials",
        199,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        CONTAINMENT,
        (
            ("biological", "Biological"),
            ("chemical", "Chemical"),
            ("nuclear-radiological", "Nuclear/Radiological"),
        ),
        {},
    ),
    "paleontology": (
        "Paleontology",
        212,
        D.HARD,
        (),
        ANALYSIS,
        (
            ("paleoanthropology", "Paleoanthropology"),
            ("paleobotany", "Paleobotany"),
            ("paleozoology", "Paleozoology"),
        ),
        {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
}
# B207 keys a Mechanic specialty to a machine type, and a machine type normally
# corresponds to a vehicle-operation specialty, so the expansion is generated
# from the specialties this module already records rather than authored a second
# time. Shiphandling is a command skill rather than vehicle operation, so it
# contributes no machine type of its own.
MECHANIC_FAMILIES: Final = ("boating", "driving", "piloting", "submarine")
# A muscle-powered hull carries no machinery for a Mechanic to work on.
MECHANIC_EXCLUDED: Final = frozenset({"boating-unpowered"})
# Families whose specialty axis is a world, a planet type, a species or a region.
# That axis is campaign data rather than a Basic Set listing, so the row records
# the axis and #390 owns the procedure that instantiates a named subject.
OPEN_FAMILIES: Final = {
    "biology": ("Biology", 180, D.VERY_HARD, _attribute(A.IQ, -6), "one planet type"),
    "disguise": ("Disguise", 187, D.AVERAGE, _attribute(A.IQ, -5), "one species or culture"),
    "geography": ("Geography", 198, D.HARD, _attribute(A.IQ, -6), "one world or region"),
    "geology": ("Geology", 198, D.HARD, _attribute(A.IQ, -6), "one planet type"),
}


def _slug(label: str) -> str:
    """The identifier a machine-type label expands to, mirroring its own name."""
    return label.casefold().replace(" ", "-").replace("/", "-")


def _mechanic_specialties() -> tuple[tuple[str, str], ...]:
    """Every powered vehicle specialty, as the machine types B207 keys Mechanic to."""
    derived = [
        (_slug(label), label)
        for family in MECHANIC_FAMILIES
        for name, label in VEHICLE_FAMILIES[family][5]
        if f"{family}-{name}" not in MECHANIC_EXCLUDED
    ]
    if len({name for name, _ in derived}) != len(derived):
        raise ValidationError("Two vehicle specialties derive the same Mechanic specialty")
    return tuple(derived)


def _family(
    family: str,
    title: str,
    page: int,
    difficulty: D,
    defaults: tuple[SkillDefault, ...],
    task: TaskClass,
    members: tuple[tuple[str, str], ...],
    context: Mapping[str, tuple[int, ...]],
) -> tuple[TechnologyProcedure, ...]:
    """One family row and the concrete specialties that complete it."""
    conditional_prerequisites = (
        (
            SkillPrerequisite(
                "skill:mathematics-applied",
                kind=PrerequisiteKind.TRAINED_SKILL,
                minimum_technology_level=5,
            ),
        )
        if family == "engineer"
        else ()
    )
    return (
        TechnologyProcedure(
            f"skill:{family}",
            title,
            page,
            A.IQ,
            difficulty,
            defaults,
            conditional_prerequisites,
            specialties=tuple(f"skill:{family}-{name}" for name, _ in members),
            resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
            transferred=dict(context),
        ),
        *(
            TechnologyProcedure(
                f"skill:{family}-{name}",
                f"{title} ({label})",
                page,
                A.IQ,
                difficulty,
                defaults,
                conditional_prerequisites,
                (
                    PrerequisiteGroup(
                        (
                            SkillPrerequisite("skill:chemistry"),
                            SkillPrerequisite("skill:metallurgy"),
                        )
                    ),
                )
                if family == "engineer" and name == "materials"
                else (),
                specialty=Specialty(family, name),
                task=task,
                resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
                transferred=dict(context),
            )
            for name, label in members
        ),
    )


def _science_rows() -> tuple[TechnologyProcedure, ...]:
    """The enumerated families, the derived Mechanic rows, and the open families."""
    rows: list[TechnologyProcedure] = []
    for family, entry in SCIENCE_FAMILIES.items():
        rows.extend(_family(family, *entry))
    rows.extend(
        _family(
            "mechanic",
            "Mechanic",
            207,
            D.AVERAGE,
            _attribute(A.IQ, -5),
            REPAIR,
            _mechanic_specialties(),
            {CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
        )
    )
    # An open family is expanded in the only way its axis allows: by recording
    # what the player names. Nothing dispatches until #390 instantiates it.
    rows.extend(
        TechnologyProcedure(
            f"skill:{family}",
            title,
            page,
            A.IQ,
            difficulty,
            defaults,
            resolved=(SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
            transferred={
                RUNTIME_PROCEDURE: (OPEN_SUBJECT_OWNER,),
                CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,),
            },
            open_subject=subject,
        )
        for family, (title, page, difficulty, defaults, subject) in OPEN_FAMILIES.items()
    )
    return tuple(rows)


_DECLARED_ROWS: Final = (
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
        transferred={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        transferred={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
            transferred={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        transferred={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "chemistry",
        "Chemistry",
        183,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "cryptography",
        "Cryptography",
        186,
        A.IQ,
        D.HARD,
        (),
        _study("message-recovered", 1),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "forensics",
        "Forensics",
        196,
        A.IQ,
        D.HARD,
        _attribute(A.IQ, -6),
        _study("clue"),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "forward-observer",
        "Forward Observer",
        196,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study("target-designation", 1),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
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
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "metallurgy",
        "Metallurgy",
        209,
        A.IQ,
        D.HARD,
        (),
        _study(),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    _task(
        "physics",
        "Physics",
        213,
        A.IQ,
        D.VERY_HARD,
        _attribute(A.IQ, -6),
        _study(),
        acquisition=(
            SkillPrerequisite(
                "skill:mathematics-applied",
                minimum_technology_level=5,
            ),
        ),
    ),
    _task(
        "research",
        "Research",
        217,
        A.IQ,
        D.AVERAGE,
        _attribute(A.IQ, -5),
        _study(),
        acquisition=(
            SkillPrerequisite("literacy", kind=PrerequisiteKind.CAPABILITY),
            SkillPrerequisite("skill:computer-operation", minimum_technology_level=8),
        ),
        context={CONDITIONAL_DEFAULTS: (CONDITIONAL_OWNER,)},
    ),
    # B169/B213 optional Physics specialty, bound through its own dispatch.
    TechnologyProcedure(
        "skill:physics-acoustics",
        "Physics (Acoustics)",
        213,
        A.IQ,
        D.HARD,
        prerequisites=(
            SkillPrerequisite(
                "skill:mathematics-applied",
                minimum_technology_level=5,
            ),
        ),
        specialty=Specialty("physics", "acoustics", "skill:physics"),
        task=_study(),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
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
    # --- Discipline-keyed families (#356) ------------------------------------
    # B180-B212 key these to a discipline, an electronics family or a machine
    # type rather than to a vehicle class. Where the source enumerates that axis
    # the family is expanded; where the axis is a world, a species or a region it
    # is recorded as an open family, because a list would be an invention.
    *_science_rows(),
)


def _recorded(entry: TechnologyProcedure) -> TechnologyProcedure:
    """Reconcile a task binding with the source-owned default record."""
    transferred = dict(entry.transferred)
    resolved = tuple(item for item in entry.resolved if item != CONDITIONAL_DEFAULTS)
    if CONDITIONAL_DEFAULTS in entry.resolved or CONDITIONAL_DEFAULTS in transferred:
        transferred.pop(CONDITIONAL_DEFAULTS, None)
        if CONTEXTUAL_DEFAULTS in recorded_blockers(entry.id):
            transferred[CONTEXTUAL_DEFAULTS] = (476,)
    return replace(
        entry,
        defaults=recorded_defaults(entry.id),
        resolved=tuple(dict.fromkeys(resolved)),
        transferred=MappingProxyType(transferred),
    )


_ROWS: Final = tuple(_recorded(entry) for entry in _DECLARED_ROWS)
# Every listed technology row, plus the concrete specialties this issue expands.
PROCEDURES: Final = MappingProxyType({entry.id: entry for entry in _ROWS})


def definitions() -> tuple[RuleDefinition, ...]:
    """Dispatchable technology skills for a new package pin; unbound rows are absent."""
    return tuple(entry.definition() for entry in _ROWS if entry.dispatchable)


# What each activation capability would have to cover before a bound row may be
# offered in play. The registry decides whether it is covered; this only says why.
ACTIVATION_DETAIL: Final = MappingProxyType(
    {
        "gurps.vehicles.movement": (
            "control loss, collision, occupant injury and restart for the "
            "locomotion mode this row operates"
        ),
    }
)


def unsupported_scope() -> tuple[tuple[str, UnsupportedScope], ...]:
    """Publish every capability a bound row needs that is not yet verified.

    The procedure executes and is tested; what is missing is the capability the
    scenario, character and LLM validators consult before offering the skill.
    Publishing it keeps that gap visible instead of leaving live play to discover
    it, and an unnamed capability is a coverage failure rather than an omission.
    """
    scope = []
    for entry in _ROWS:
        for identifier in entry.activation_blockers:
            detail = ACTIVATION_DETAIL.get(identifier)
            if detail is None:
                raise ValidationError(f"Activation blocker names no scope: {identifier}")
            scope.append((entry.id, UnsupportedScope(identifier, detail, CAPABILITY_OWNER)))
    return tuple(scope)


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
