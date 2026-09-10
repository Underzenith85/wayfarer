"""Executable ranged combat skill procedures (#344); unbound rows stay blocked.

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B168-233 skill chapter with the B301-304 index. The frozen
first-printing/2007-01-26 errata delta stays #191's blocker on every row, so a
bound procedure is reported as implemented and is still not certified.

Recording a skill never makes it playable. A row is implemented only when this
module binds it to the ranged dispatch that already resolves it
(`orchestration/gurps_ranged`), declares the exact weapon modes it governs, and
names a registered capability. Every other listed row keeps its recorded
blockers and names the concrete open child issue that owns them: #354
entangling attacks, #355 TL-indexed firearms and beams, #357 crew-served and
vehicle-mounted weapons, #359 liquid projector streams, #360 the spear-thrower
launcher, #361 Innate Attack specialties, #362 cross-specialty and conditional
defaults.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
)
from wayfarer.rules.conformance import CoverageStatus, capability, profile
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import SkillDefault, SkillSpec, Specialty

PROFILE: Final = "gurps-basic-set-4e-2004"
OWNER: Final = 344
CAPABILITIES: Final = ("gurps.combat.ranged_attack", "gurps.combat.ranged_weapon_skills")
RUNTIME_PROCEDURE: Final = "runtime-procedure"
SPECIALTY_EXPANSION: Final = "specialty-expansion"
CONDITIONAL_DEFAULTS: Final = "conditional-or-skill-defaults"
TECHNOLOGY_LEVEL: Final = "technology-level-context"
Hands = Literal[1, 2]


@dataclass(frozen=True, slots=True)
class WeaponClass:
    """The exact ranged modes one skill governs; anything else is another skill.

    These are mode shapes, not table values: whether the projectile is the item
    itself, whether a pinned ammunition reference is required, and the ceilings
    that separate a muscle-powered missile from the firearms #355 owns. A skill
    that accepted any ranged mode would be generic target calculation, not
    whole-entry support.
    """

    thrown: bool
    ammunition: bool
    maximum_rate_of_fire: int = 1
    maximum_recoil: int = 1
    hands: tuple[Hands, ...] = (1, 2)


LAUNCHER: Final = WeaponClass(thrown=False, ammunition=True)
BOW: Final = WeaponClass(thrown=False, ammunition=True, hands=(2,))
THROWN: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,))


@dataclass(frozen=True, slots=True)
class RangedProcedure:
    """One accounted-for ranged combat row and the dispatch it does or does not have."""

    id: str
    name: str
    page: int
    attribute: A
    difficulty: D
    defaults: tuple[SkillDefault, ...] = ()
    weapon: WeaponClass | None = None
    specialty: Specialty | None = None
    # A family row is never dispatched; it is completed by its concrete specialties.
    specialties: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    owners: tuple[int, ...] = ()

    @property
    def implemented(self) -> bool:
        """A bound dispatch executes; recording a procedure never implements it."""
        return RUNTIME_PROCEDURE in self.resolved

    @property
    def dispatchable(self) -> bool:
        return self.implemented and self.weapon is not None

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    def spec(self) -> SkillSpec:
        return SkillSpec(
            self.attribute,
            self.difficulty,
            self.reference,
            self.defaults,
            specialty=self.specialty,
        )

    def definition(self) -> RuleDefinition:
        if not self.dispatchable:
            raise ValidationError(f"Ranged skill has no bound dispatch: {self.id}")
        return RuleDefinition(
            self.id,
            DefinitionKind.SKILL,
            self.name,
            source(PROFILE).id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "combat.ranged-attack"),
            skill=self.spec(),
        )


def _thrown(name: str, title: str) -> RangedProcedure:
    """One concrete Thrown Weapon specialty (B226); specialties never infer each other."""
    return RangedProcedure(
        f"skill:thrown-weapon-{name}",
        f"Thrown Weapon ({title})",
        226,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        THROWN,
        Specialty("thrown-weapon", name),
        resolved=(RUNTIME_PROCEDURE,),
        # B226 also records a default from the matching melee weapon skill. That
        # value is not in the frozen inventory and is not reconstructed here.
        blockers=(CONDITIONAL_DEFAULTS,),
        owners=(362,),
    )


THROWN_SPECIALTIES: Final = (
    _thrown("axe-mace", "Axe/Mace"),
    _thrown("dart", "Dart"),
    _thrown("harpoon", "Harpoon"),
    _thrown("knife", "Knife"),
    _thrown("shuriken", "Shuriken"),
    _thrown("spear", "Spear"),
    _thrown("stick", "Stick"),
)

_ROWS: Final = (
    # Muscle-powered launchers: a pinned missile, one shot, no recoil ladder.
    RangedProcedure(
        "skill:bow",
        "Bow",
        182,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5),),
        BOW,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    RangedProcedure(
        "skill:crossbow",
        "Crossbow",
        186,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        LAUNCHER,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    RangedProcedure(
        "skill:sling",
        "Sling",
        221,
        A.DX,
        D.HARD,
        (SkillDefault(A.DX, -6),),
        LAUNCHER,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    RangedProcedure(
        "skill:blowpipe",
        "Blowpipe",
        180,
        A.DX,
        D.HARD,
        (SkillDefault(A.DX, -6),),
        LAUNCHER,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    # Family row: expanded into the concrete specialties above and never dispatched.
    RangedProcedure(
        "skill:thrown-weapon",
        "Thrown Weapon",
        226,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(entry.id for entry in THROWN_SPECIALTIES),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION),
        blockers=(CONDITIONAL_DEFAULTS,),
        owners=(362,),
    ),
    *THROWN_SPECIALTIES,
    # Transferred rows. Each keeps its recorded blockers and names its owner.
    RangedProcedure(
        "skill:bolas",
        "Bolas",
        181,
        A.DX,
        D.AVERAGE,
        blockers=(RUNTIME_PROCEDURE,),
        owners=(354,),
    ),
    RangedProcedure(
        "skill:net",
        "Net",
        211,
        A.DX,
        D.HARD,
        blockers=(RUNTIME_PROCEDURE, CONDITIONAL_DEFAULTS),
        owners=(354, 362),
    ),
    RangedProcedure(
        "skill:spear-thrower",
        "Spear Thrower",
        222,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5),),
        blockers=(RUNTIME_PROCEDURE, CONDITIONAL_DEFAULTS),
        owners=(360, 362),
    ),
    RangedProcedure(
        "skill:guns",
        "Guns",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        owners=(355,),
    ),
    RangedProcedure(
        "skill:beam-weapons",
        "Beam Weapons",
        179,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        owners=(355,),
    ),
    RangedProcedure(
        "skill:artillery",
        "Artillery",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        owners=(357,),
    ),
    RangedProcedure(
        "skill:gunner",
        "Gunner",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        owners=(357,),
    ),
    RangedProcedure(
        "skill:liquid-projector",
        "Liquid Projector",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
        owners=(359,),
    ),
    RangedProcedure(
        "skill:innate-attack",
        "Innate Attack",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        blockers=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION),
        owners=(361,),
    ),
)
# Every listed ranged combat row, plus the concrete specialties this issue expands.
PROCEDURES: Final = MappingProxyType({entry.id: entry for entry in _ROWS})


def definitions() -> tuple[RuleDefinition, ...]:
    """Dispatchable ranged skills for a new package pin; unbound rows are absent."""
    return tuple(entry.definition() for entry in _ROWS if entry.dispatchable)


def require_capability(profile_id: str, capability_id: str) -> None:
    """Use the registry, never a typed weapon or a manual ruling, to claim support."""
    declared = capability(capability_id)
    if capability_id not in profile(profile_id).required_capabilities:
        raise ValidationError(f"Rules capability outside profile: {capability_id}")
    if declared.status is CoverageStatus.ABSENT:
        raise ValidationError(f"Rules capability has no coverage: {capability_id}")


def require_mode(
    profile_id: str,
    skill_id: str,
    *,
    ranged: bool,
    thrown: bool,
    ammunition: bool,
    rate_of_fire: int,
    recoil: int,
    hands: int,
    tight_beam: bool,
) -> RangedProcedure | None:
    """Fail closed before dice when a weapon claims an unbound ranged skill.

    Skills outside this audit are not this module's business and pass through.
    """
    entry = PROCEDURES.get(skill_id)
    if entry is None:
        return None
    if profile_id != PROFILE:
        raise ValidationError(
            f"Ranged combat skill requires the exact Basic Set profile: {skill_id}"
        )
    if entry.specialties:
        raise ValidationError(
            f"Ranged skill family requires a concrete specialty: {skill_id}: "
            + ", ".join(entry.specialties)
        )
    if not entry.dispatchable:
        raise ValidationError(
            f"Ranged skill procedure is unsupported: {skill_id}: "
            + ", ".join(entry.blockers)
            + " ("
            + ", ".join(f"#{issue}" for issue in entry.owners)
            + ")"
        )
    for capability_id in CAPABILITIES:
        require_capability(profile_id, capability_id)
    weapon = entry.weapon
    assert weapon is not None
    if not ranged or tight_beam:
        raise ValidationError(f"Ranged skill cannot resolve this weapon mode: {skill_id}")
    if thrown != weapon.thrown or ammunition != weapon.ammunition:
        raise ValidationError(f"Weapon mode is outside the skill's class: {skill_id}")
    if rate_of_fire > weapon.maximum_rate_of_fire or recoil > weapon.maximum_recoil:
        raise ValidationError(f"Rapid fire and recoil are outside the skill's class: {skill_id}")
    if hands not in weapon.hands:
        raise ValidationError(f"Weapon grip is outside the skill's class: {skill_id}")
    return entry
