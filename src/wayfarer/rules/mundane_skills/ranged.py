"""Executable ranged combat skill procedures (#344); unbound rows stay blocked.

Numeric constructions: Basic Set Characters, Fourth Edition, third printing,
B168-233 skill chapter with the B301-304 index. Selected-printing source identity
is tracked separately from remaining mechanics gaps.

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

# The same shape #345 publishes, so one report field carries both groups.
from wayfarer.rules.mundane_skills.social import UnsupportedScope
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
    # Whether the mode must carry pinned mount facts. A crew-served or
    # vehicle-mounted skill needs them; a held weapon's skill must not have them.
    mounted: bool = False
    # Whether the mode must carry pinned stream facts. Only the liquid projector
    # rows hold a stream open; every other ranged skill fires and is done.
    spraying: bool = False
    # Whether the throw is launcher-assisted. Only the spear thrower row throws
    # with a separate held launcher; every other thrown row uses the item alone.
    launched: bool = False
    # The only B270 rated weapon ST (#348) this skill may carry, if any.
    rated_kind: Literal["bow", "crossbow"] | None = None


LAUNCHER: Final = WeaponClass(thrown=False, ammunition=True)
# A binding is thrown and must carry its pinned entangling facts (#354).
BOLAS: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,), entangling=True)
NET: Final = WeaponClass(thrown=True, ammunition=False, entangling=True)
BOW: Final = WeaponClass(thrown=False, ammunition=True, hands=(2,), rated_kind="bow")
CROSSBOW: Final = WeaponClass(thrown=False, ammunition=True, rated_kind="crossbow")
THROWN: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,))
# B222: a throw made with a separate held launcher, never a bare thrown spear.
LAUNCHED: Final = WeaponClass(thrown=True, ammunition=False, hands=(1,), launched=True)
# B201: an innate attack comes from the creature. There is no item to reserve,
# no missile to reload, and no grip that limits it.
INNATE: Final = WeaponClass(thrown=False, ammunition=False, tight_beam=True)
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
# Crew-served and vehicle-mounted weapons (#357). The mount bears the weapon,
# so the firer's own grip and ST are not what validate the shot.
MOUNTED: Final = WeaponClass(
    thrown=False,
    ammunition=True,
    maximum_rate_of_fire=100,
    maximum_recoil=20,
    technology_level_indexed=True,
    tight_beam=True,
    mounted=True,
)
# Liquid projectors (#359): a held stream, never a thrown or rapid-fire shot.
SPRAYER: Final = WeaponClass(
    thrown=False,
    ammunition=True,
    technology_level_indexed=True,
    tight_beam=False,
    spraying=True,
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


def defaults_gap(family: str | None) -> UnsupportedScope:
    """State exactly what a row's unrecorded default leaves open, for #362.

    The frozen inventory records that the source states a default for this row
    which is conditional or comes from another skill, and that neither its
    source skill nor its modifier is recorded. Reading either off a different
    printing would defeat the baseline the audit exists to protect, so the gap
    is published with the sibling rows a cross-specialty default would run
    between rather than reconstructed into a runnable roll.
    """
    return UnsupportedScope(
        "unrecorded-default",
        (
            "The source states a conditional or cross-skill default for this row."
            " Neither the skill it comes from nor its modifier is recorded, and"
            " neither is inferred here."
            + (
                f" A cross-specialty default would run between the {family} specialties."
                if family
                else ""
            )
        ),
        362,
    )


# B205 liquid projectors are held streams laid on one target per second. They are
# not area attacks: walking the stream changes its one target on a later second.
# Lingering ignition is scheduled by the encounter resolver (#398), so this
# family has no remaining stream-specific published scope.


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
    # Named parts of a bound entry this module does not carry. A bound row can
    # still leave scope to another issue; publishing it keeps the gap visible
    # instead of folding it back into a blocker.
    unsupported: tuple[UnsupportedScope, ...] = ()

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(self.transferred)

    @property
    def published(self) -> tuple[UnsupportedScope, ...]:
        """Named scope this row leaves open, including its unrecorded default."""
        gap = (
            (defaults_gap(self.specialty.family if self.specialty else None),)
            if CONDITIONAL_DEFAULTS in self.transferred
            else ()
        )
        return self.unsupported + gap

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
        LAUNCHED,
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
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
    # B178/B198: crew-served and vehicle-mounted families, expanded into
    # concrete specialties that each fire from a mount rather than a grip (#357).
    RangedProcedure(
        "skill:artillery",
        "Artillery",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        specialties=tuple(
            f"skill:artillery-{key}"
            for key in ("beams", "bombs", "cannon", "catapult", "guided-missile", "torpedoes")
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    RangedProcedure(
        "skill:artillery-beams",
        "Artillery (Beams)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "beams"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery-bombs",
        "Artillery (Bombs)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "bombs"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery-cannon",
        "Artillery (Cannon)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "cannon"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery-catapult",
        "Artillery (Catapult)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "catapult"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery-guided-missile",
        "Artillery (Guided Missile)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "guided-missile"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:artillery-torpedoes",
        "Artillery (Torpedoes)",
        178,
        A.IQ,
        D.AVERAGE,
        (SkillDefault(A.IQ, -5),),
        MOUNTED,
        Specialty("artillery", "torpedoes"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:gunner",
        "Gunner",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(
            f"skill:gunner-{key}"
            for key in ("beams", "cannon", "machine-gun", "rockets", "torpedoes")
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    RangedProcedure(
        "skill:gunner-beams",
        "Gunner (Beams)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        MOUNTED,
        Specialty("gunner", "beams"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:gunner-cannon",
        "Gunner (Cannon)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        MOUNTED,
        Specialty("gunner", "cannon"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:gunner-machine-gun",
        "Gunner (Machine Gun)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        MOUNTED,
        Specialty("gunner", "machine-gun"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:gunner-rockets",
        "Gunner (Rockets)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        MOUNTED,
        Specialty("gunner", "rockets"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:gunner-torpedoes",
        "Gunner (Torpedoes)",
        198,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        MOUNTED,
        Specialty("gunner", "torpedoes"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    # B205: a family of held streams, expanded into concrete specialties (#359).
    RangedProcedure(
        "skill:liquid-projector",
        "Liquid Projector",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(
            f"skill:liquid-projector-{key}"
            for key in ("flamethrower", "sprayer", "squirt-gun", "water-cannon")
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION, TECHNOLOGY_LEVEL),
    ),
    RangedProcedure(
        "skill:liquid-projector-flamethrower",
        "Liquid Projector (Flamethrower)",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        SPRAYER,
        Specialty("liquid-projector", "flamethrower"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:liquid-projector-sprayer",
        "Liquid Projector (Sprayer)",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        SPRAYER,
        Specialty("liquid-projector", "sprayer"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:liquid-projector-squirt-gun",
        "Liquid Projector (Squirt Gun)",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        SPRAYER,
        Specialty("liquid-projector", "squirt-gun"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:liquid-projector-water-cannon",
        "Liquid Projector (Water Cannon)",
        205,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        SPRAYER,
        Specialty("liquid-projector", "water-cannon"),
        resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    # B201: the attack comes from the creature, so each specialty is a delivery
    # rather than a weapon class (#361).
    RangedProcedure(
        "skill:innate-attack",
        "Innate Attack",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        specialties=tuple(
            f"skill:innate-attack-{key}" for key in ("beam", "breath", "gaze", "projectile")
        ),
        resolved=(RUNTIME_PROCEDURE, SPECIALTY_EXPANSION),
    ),
    RangedProcedure(
        "skill:innate-attack-beam",
        "Innate Attack (Beam)",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        INNATE,
        Specialty("innate-attack", "beam"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:innate-attack-breath",
        "Innate Attack (Breath)",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        INNATE,
        Specialty("innate-attack", "breath"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:innate-attack-gaze",
        "Innate Attack (Gaze)",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        INNATE,
        Specialty("innate-attack", "gaze"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
    RangedProcedure(
        "skill:innate-attack-projectile",
        "Innate Attack (Projectile)",
        201,
        A.DX,
        D.EASY,
        (SkillDefault(A.DX, -4),),
        INNATE,
        Specialty("innate-attack", "projectile"),
        resolved=(RUNTIME_PROCEDURE,),
        transferred={CONDITIONAL_DEFAULTS: (362,)},
    ),
)
# Every listed ranged combat row, plus the concrete specialties this issue expands.
PROCEDURES: Final = MappingProxyType({entry.id: entry for entry in _ROWS})


def ranged_scope() -> tuple[tuple[str, UnsupportedScope], ...]:
    """Named scope a bound ranged row leaves to another issue, for the report."""
    return tuple(
        (entry.id, scope) for entry in _ROWS for scope in entry.published if entry.implemented
    )


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
    mounted: bool = False,
    spraying: bool = False,
    launched: bool = False,
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
    if mounted != weapon.mounted:
        raise ValidationError(f"Mount facts are outside the skill's class: {skill_id}")
    if spraying != weapon.spraying:
        raise ValidationError(f"Stream facts are outside the skill's class: {skill_id}")
    if launched != weapon.launched:
        raise ValidationError(f"Launcher facts are outside the skill's class: {skill_id}")
    if (
        weapon.conventional_firearm is not None
        and conventional_firearm != weapon.conventional_firearm
    ):
        raise ValidationError(f"Firearm metadata is outside the skill's class: {skill_id}")
    # B270 rated weapon ST (#348) belongs to the launcher its own skill governs.
    if rated_kind != weapon.rated_kind and not (rated_kind is None and weapon.rated_kind):
        raise ValidationError(f"Rated weapon ST is outside the skill's class: {skill_id}")
    return entry
