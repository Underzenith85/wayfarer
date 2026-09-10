"""Executable ranged combat skill procedures (#344); unbound rows stay blocked.

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B168-233 skill chapter with the B301-304 index. The frozen
first-printing/2007-01-26 errata delta stays #191's blocker on every row, so a
bound procedure is reported as implemented and is still not certified.

Recording a skill never makes it playable. A row is implemented only when this
module binds it to the ranged dispatch that already resolves it
(`orchestration/gurps_ranged`), declares the exact weapon modes it governs, and
names a registered capability. Every other listed row keeps its recorded
blockers and names the concrete open child issue that owns them: #355
TL-indexed firearms and beams, #357 crew-served and vehicle-mounted weapons,
#359 liquid projector streams, #360 the spear-thrower launcher, #361 Innate
Attack specialties, #362 cross-specialty and conditional defaults.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
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
DISPATCH: Final = "combat.ranged-attack"
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
    # Whether the mode must carry pinned entangling facts. A skill that binds
    # its target needs them; every other ranged skill must not have them.
    entangling: bool = False
    # A TL-indexed skill only dispatches a weapon of the campaign's pinned
    # technology level; cross-TL familiarity is not approximated here.
    technology_level_indexed: bool = False
    # Tight-beam damage belongs to the beam weapon rows and to nothing else.
    tight_beam: bool = False
    # Conventional firearm metadata (#372) belongs to conventional firearms.
    conventional_firearm: bool | None = None
    # The only B270 rated weapon ST (#348) this skill may carry, if any.
    rated_kind: Literal["bow", "crossbow"] | None = None


LAUNCHER: Final = WeaponClass(thrown=False, ammunition=True)
# A binding is thrown and must carry its pinned entangling facts (#354).
BOLAS: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,), entangling=True)
NET: Final = WeaponClass(thrown=True, ammunition=False, entangling=True)
BOW: Final = WeaponClass(thrown=False, ammunition=True, hands=(2,), rated_kind="bow")
CROSSBOW: Final = WeaponClass(thrown=False, ammunition=True, rated_kind="crossbow")
THROWN: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,))
# TL-indexed personal weapons (#355). Rapid fire and recoil are the mode's own
# pinned facts, so these ceilings are the engine's supported bounds, not a
# table value. A beam is never a conventional firearm and a gun is never a beam.
GUN: Final = WeaponClass(
    thrown=False,
    ammunition=True,
    maximum_rate_of_fire=100,
    maximum_recoil=20,
    technology_level_indexed=True,
    conventional_firearm=None,
)
BEAM: Final = WeaponClass(
    thrown=False,
    ammunition=True,
    maximum_rate_of_fire=100,
    maximum_recoil=20,
    technology_level_indexed=True,
    tight_beam=True,
    conventional_firearm=False,
)


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
        return self.implemented and self.weapon is not None

    @property
    def dispatch(self) -> str | None:
        return DISPATCH if self.dispatchable else None

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
            hooks=("character.gurps-skill", DISPATCH),
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
        transferred={CONDITIONAL_DEFAULTS: (362,)},
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
        CROSSBOW,
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
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    *THROWN_SPECIALTIES,
    # Transferred rows. Each keeps its recorded blockers and names its owner.
    # B181/B211: a landed binding holds the target; the outcome is the
    # entanglement, not the hit points it may also cost.
    RangedProcedure(
        "skill:bolas",
        "Bolas",
        181,
        A.DX,
        D.AVERAGE,
        weapon=BOLAS,
        resolved=(RUNTIME_PROCEDURE,),
    ),
    RangedProcedure(
        "skill:net",
        "Net",
        211,
        A.DX,
        D.HARD,
        weapon=NET,
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:spear-thrower",
        "Spear Thrower",
        222,
        A.DX,
        D.AVERAGE,
        (SkillDefault(A.DX, -5),),
        transferred={RUNTIME_PROCEDURE: (360,), CONDITIONAL_DEFAULTS: (362,)},
    ),
    # B198/B179: TL-indexed families, expanded into concrete specialties that
    # each dispatch a weapon of the campaign's own technology level (#355).
    RangedProcedure(
        "skill:guns",
        "Guns",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(
            f"skill:guns-{key}"
            for key in (
                "pistol",
                "rifle",
                "shotgun",
                "submachine-gun",
                "light-machine-gun",
                "musket",
                "grenade-launcher",
                "light-anti-armor-weapon",
            )
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    RangedProcedure(
        "skill:guns-pistol",
        "Guns (Pistol)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "pistol"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-rifle",
        "Guns (Rifle)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "rifle"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-shotgun",
        "Guns (Shotgun)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "shotgun"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-submachine-gun",
        "Guns (Submachine Gun)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "submachine-gun"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-light-machine-gun",
        "Guns (Light Machine Gun)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "light-machine-gun"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-musket",
        "Guns (Musket)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "musket"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-grenade-launcher",
        "Guns (Grenade Launcher)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "grenade-launcher"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:guns-light-anti-armor-weapon",
        "Guns (Light Anti-Armor Weapon)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        GUN,
        Specialty("guns", "light-anti-armor-weapon"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:beam-weapons",
        "Beam Weapons",
        179,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(f"skill:beam-weapons-{key}" for key in ("pistol", "rifle", "projector")),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    RangedProcedure(
        "skill:beam-weapons-pistol",
        "Beam Weapons (Pistol)",
        179,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        BEAM,
        Specialty("beam-weapons", "pistol"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:beam-weapons-rifle",
        "Beam Weapons (Rifle)",
        179,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        BEAM,
        Specialty("beam-weapons", "rifle"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:beam-weapons-projector",
        "Beam Weapons (Projector)",
        179,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        BEAM,
        Specialty("beam-weapons", "projector"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery",
        "Artillery",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        transferred=dict.fromkeys(
            (RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL), (357,)
        ),
    ),
    RangedProcedure(
        "skill:gunner",
        "Gunner",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        transferred=dict.fromkeys(
            (RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL), (357,)
        ),
    ),
    RangedProcedure(
        "skill:liquid-projector",
        "Liquid Projector",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        transferred=dict.fromkeys(
            (RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL), (359,)
        ),
    ),
    RangedProcedure(
        "skill:innate-attack",
        "Innate Attack",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        transferred={RUNTIME_PROCEDURE: (361,), SPECIALTY_EXPANSION: (361,)},
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


def require_technology(skill_id: str, campaign: int | None, weapon: int) -> None:
    """A TL-indexed skill needs a campaign technology level and a matching weapon.

    Cross-TL familiarity is a separate construction that this repository does
    not carry, so an out-of-era weapon fails closed instead of being resolved
    with an invented penalty (#355).
    """
    entry = PROCEDURES.get(skill_id)
    if entry is None or entry.weapon is None or not entry.weapon.technology_level_indexed:
        return
    if campaign is None:
        raise ValidationError(
            f"TL-indexed ranged skill requires a pinned campaign technology level: {skill_id}"
        )
    if weapon != campaign:
        raise ValidationError(f"Weapon technology level is outside the campaign's era: {skill_id}")


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
    rated_kind: str | None = None,
    entangling: bool = False,
    conventional_firearm: bool = False,
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
            + ", ".join(
                f"{blocker} (" + ", ".join(f"#{issue}" for issue in owners) + ")"
                for blocker, owners in entry.transferred.items()
            )
        )
    for capability_id in CAPABILITIES:
        require_capability(profile_id, capability_id)
    weapon = entry.weapon
    assert weapon is not None
    if not ranged or (tight_beam and not weapon.tight_beam):
        raise ValidationError(f"Ranged skill cannot resolve this weapon mode: {skill_id}")
    if thrown != weapon.thrown or ammunition != weapon.ammunition:
        raise ValidationError(f"Weapon mode is outside the skill's class: {skill_id}")
    if rate_of_fire > weapon.maximum_rate_of_fire or recoil > weapon.maximum_recoil:
        raise ValidationError(f"Rapid fire and recoil are outside the skill's class: {skill_id}")
    if hands not in weapon.hands:
        raise ValidationError(f"Weapon grip is outside the skill's class: {skill_id}")
    if entangling != weapon.entangling:
        raise ValidationError(f"Entangling facts are outside the skill's class: {skill_id}")
    if (
        weapon.conventional_firearm is not None
        and conventional_firearm != weapon.conventional_firearm
    ):
        raise ValidationError(f"Firearm metadata is outside the skill's class: {skill_id}")
    # B270 rated weapon ST (#348) belongs to the launcher its own skill governs.
    if rated_kind != weapon.rated_kind and not (rated_kind is None and weapon.rated_kind):
        raise ValidationError(f"Rated weapon ST is outside the skill's class: {skill_id}")
    return entry
