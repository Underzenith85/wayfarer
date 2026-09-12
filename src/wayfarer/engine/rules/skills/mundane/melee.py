"""Combat skill procedures from the Basic Set skill chapter (#339).

The inventory remains the source of truth for attributes, difficulty, defaults,
specialties and references.  This module supplies the missing executable
contract: which split simulation service consumes each skill, the observable
effect it produces, and (for equipment-bound skills) the weapon shape it may
claim.  Family rows never dispatch without a concrete specialty.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.conformance import CoverageStatus, capability, profile
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import Resolution as R
from wayfarer.engine.rules.skills.mundane.procedures import Task, bind
from wayfarer.errors import ValidationError

OWNER: Final = 339
PROFILE: Final = "gurps-basic-set-4e-2004"
CAPABILITIES: Final = (
    "gurps.combat.melee_attack",
    "gurps.combat.active_defense",
    "gurps.combat.melee_weapon_skills",
)

MELEE: Final = "combat.melee-attack"
UNARMED: Final = "combat.unarmed"
DEFENSE: Final = "combat.active-defense"
READY: Final = "combat.ready"
TACTICAL: Final = "combat.tactical"
NONCOMBAT: Final = "noncombat.approach"


def task(
    dispatch: str,
    effect: str,
    unit: str,
    *,
    resolution: R = R.SUCCESS_ROLL,
    policy: P = P.RETRY_UNTIL_SUCCESS,
    context: tuple[str, ...] = (),
    modifiers: frozenset[str] = frozenset(),
    margin: bool = False,
) -> Task:
    return Task(
        dispatch,
        effect,
        policy,
        unit,
        resolution,
        context,
        modifiers,
        int(margin),
    )


ATTACK = frozenset({"maneuver", "posture", "visibility", "hit-location", "deceptive"})
DEFEND = frozenset({"retreat", "posture", "multiple-parry", "attack-quality"})
GRAPPLE = frozenset({"relative-size", "posture", "encumbrance", "grip"})
READYING = frozenset({"distraction", "posture", "equipment-quality"})
COMMAND = frozenset({"force-size", "intelligence-quality", "preparation", "terrain"})


@dataclass(frozen=True, slots=True)
class WeaponClass:
    """Mode properties a melee skill is permitted to claim."""

    hands: tuple[int, ...] = (1, 2)
    permits_parry: bool = True
    fencing: bool | None = None
    permits_unbalanced: bool = False


ONE_HAND = WeaponClass(hands=(1,))
TWO_HAND = WeaponClass(hands=(2,))
FENCING = WeaponClass(hands=(1,), fencing=True)
UNBALANCED = WeaponClass(permits_unbalanced=True)
ONE_HAND_UNBALANCED = WeaponClass(hands=(1,), permits_unbalanced=True)
TWO_HAND_UNBALANCED = WeaponClass(hands=(2,), permits_unbalanced=True)
NO_PARRY = WeaponClass(hands=(1, 2), permits_parry=False)

WEAPON_CLASSES: Final = MappingProxyType(
    {
        "skill:axe-mace": UNBALANCED,
        "skill:brawling": ONE_HAND,
        "skill:broadsword": UNBALANCED,
        "skill:flail": ONE_HAND_UNBALANCED,
        "skill:force-sword": ONE_HAND,
        "skill:force-whip": ONE_HAND_UNBALANCED,
        "skill:jitte-sai": ONE_HAND,
        "skill:knife": ONE_HAND,
        "skill:kusari": UNBALANCED,
        "skill:lance": NO_PARRY,
        "skill:main-gauche": FENCING,
        "skill:monowire-whip": ONE_HAND_UNBALANCED,
        "skill:polearm": TWO_HAND_UNBALANCED,
        "skill:rapier": FENCING,
        "skill:saber": FENCING,
        "skill:shortsword": ONE_HAND,
        "skill:smallsword": FENCING,
        "skill:spear": UNBALANCED,
        "skill:staff": TWO_HAND_UNBALANCED,
        "skill:tonfa": ONE_HAND,
        "skill:two-handed-axe-mace": TWO_HAND_UNBALANCED,
        "skill:two-handed-flail": TWO_HAND_UNBALANCED,
        "skill:two-handed-sword": TWO_HAND_UNBALANCED,
        "skill:whip": ONE_HAND_UNBALANCED,
    }
)


def weapon(effect: str) -> Task:
    return task(
        MELEE,
        effect,
        "attack-resolution",
        context=("readied-matching-weapon", "target-in-reach"),
        modifiers=ATTACK,
    )


def unarmed(effect: str) -> Task:
    return task(
        UNARMED,
        effect,
        "combat-resolution",
        context=("target-in-reach",),
        modifiers=ATTACK | GRAPPLE,
    )


def defend(effect: str) -> Task:
    return task(
        DEFENSE,
        effect,
        "defense-resolution",
        context=("incoming-attack",),
        modifiers=DEFEND,
    )


# Each tuple is deliberately item-specific.  Shared helpers standardize the
# service boundary, but do not erase the skill's distinct action or outcome.
_ROWS: Final = (
    ("skill:axe-mace", "Axe/Mace", weapon("strike-with-axe-or-mace")),
    ("skill:boxing", "Boxing", unarmed("box-or-parry")),
    ("skill:brawling", "Brawling", unarmed("strike-bite-or-parry")),
    ("skill:broadsword", "Broadsword", weapon("strike-with-broadsword")),
    ("skill:cloak", "Cloak", defend("parry-or-entangle-with-cloak")),
    (
        "skill:combat-art",
        "Combat Art",
        task(
            NONCOMBAT,
            "practice-selected-combat-art",
            "performance-step",
            context=("practice-context",),
            modifiers=frozenset({"rehearsal", "audience", "equipment-quality"}),
            margin=True,
        ),
    ),
    (
        "skill:combat-sport",
        "Combat Sport",
        task(
            TACTICAL,
            "compete-in-selected-combat-sport",
            "sporting-margin",
            resolution=R.QUICK_CONTEST,
            context=("opponent-present",),
            modifiers=ATTACK,
            margin=True,
        ),
    ),
    ("skill:fast-draw", "Fast-Draw", None),
    (
        "skill:fast-draw-force-sword",
        "Fast-Draw (Force Sword)",
        task(
            READY,
            "ready-force-sword",
            "readied-item",
            context=("matching-sheathed-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-knife",
        "Fast-Draw (Knife)",
        task(
            READY,
            "ready-knife",
            "readied-item",
            context=("matching-sheathed-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-long-arm",
        "Fast-Draw (Long Arm)",
        task(
            READY,
            "ready-long-arm",
            "readied-item",
            context=("matching-slung-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-pistol",
        "Fast-Draw (Pistol)",
        task(
            READY,
            "ready-pistol",
            "readied-item",
            context=("matching-holstered-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-sword",
        "Fast-Draw (Sword)",
        task(
            READY,
            "ready-sword",
            "readied-item",
            context=("matching-sheathed-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-two-handed-sword",
        "Fast-Draw (Two-Handed Sword)",
        task(
            READY,
            "ready-two-handed-sword",
            "readied-item",
            context=("matching-sheathed-item",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-arrow",
        "Fast-Draw (Arrow)",
        task(
            READY,
            "ready-arrow",
            "readied-projectile",
            context=("matching-ammunition",),
            modifiers=READYING,
        ),
    ),
    (
        "skill:fast-draw-ammo",
        "Fast-Draw (Ammo)",
        task(
            READY,
            "ready-ammunition",
            "readied-projectile",
            context=("matching-ammunition",),
            modifiers=READYING,
        ),
    ),
    ("skill:flail", "Flail", weapon("strike-with-flail")),
    ("skill:force-sword", "Force Sword", weapon("strike-with-force-sword")),
    ("skill:force-whip", "Force Whip", weapon("strike-with-force-whip")),
    ("skill:garrote", "Garrote", unarmed("choke-with-garrote")),
    ("skill:jitte-sai", "Jitte/Sai", weapon("strike-or-trap-with-jitte-sai")),
    ("skill:judo", "Judo", unarmed("throw-grapple-or-parry")),
    ("skill:karate", "Karate", unarmed("strike-or-parry")),
    ("skill:knife", "Knife", weapon("strike-with-knife")),
    ("skill:kusari", "Kusari", weapon("strike-or-entangle-with-kusari")),
    ("skill:lance", "Lance", weapon("mounted-lance-attack")),
    ("skill:lasso", "Lasso", unarmed("entangle-with-lasso")),
    ("skill:main-gauche", "Main-Gauche", weapon("strike-or-fence-with-main-gauche")),
    (
        "skill:melee-weapon",
        "Melee Weapon",
        task(
            MELEE,
            "attack-with-selected-melee-class",
            "attack-resolution",
            context=("readied-matching-weapon", "target-in-reach"),
            modifiers=ATTACK,
        ),
    ),
    ("skill:monowire-whip", "Monowire Whip", weapon("strike-with-monowire-whip")),
    ("skill:parry-missile-weapons", "Parry Missile Weapons", defend("parry-missile")),
    ("skill:polearm", "Polearm", weapon("strike-with-polearm")),
    ("skill:rapier", "Rapier", weapon("fence-with-rapier")),
    ("skill:saber", "Saber", weapon("fence-with-saber")),
    ("skill:shield", "Shield", None),
    ("skill:shield-standard", "Shield (Shield)", defend("block-with-shield")),
    ("skill:shield-buckler", "Shield (Buckler)", defend("block-with-buckler")),
    ("skill:shield-force", "Shield (Force)", defend("block-with-force-shield")),
    ("skill:shortsword", "Shortsword", weapon("strike-with-shortsword")),
    ("skill:smallsword", "Smallsword", weapon("fence-with-smallsword")),
    (
        "skill:soldier",
        "Soldier",
        task(
            NONCOMBAT,
            "perform-routine-military-duty",
            "duty-step",
            context=("military-context",),
            modifiers=COMMAND,
            margin=True,
        ),
    ),
    ("skill:spear", "Spear", weapon("strike-with-spear")),
    ("skill:staff", "Staff", weapon("strike-or-parry-with-staff")),
    (
        "skill:stage-combat",
        "Stage Combat",
        task(
            NONCOMBAT,
            "perform-safe-choreographed-combat",
            "performance-step",
            context=("choreography", "partner-present"),
            modifiers=frozenset({"rehearsal", "audience", "equipment-quality"}),
            margin=True,
        ),
    ),
    ("skill:strategy", "Strategy", None),
    (
        "skill:strategy-land",
        "Strategy (Land)",
        task(
            TACTICAL,
            "plan-land-campaign",
            "strategic-margin",
            resolution=R.QUICK_CONTEST,
            context=("opposing-command",),
            modifiers=COMMAND,
            margin=True,
        ),
    ),
    (
        "skill:strategy-naval",
        "Strategy (Naval)",
        task(
            TACTICAL,
            "plan-naval-campaign",
            "strategic-margin",
            resolution=R.QUICK_CONTEST,
            context=("opposing-command",),
            modifiers=COMMAND,
            margin=True,
        ),
    ),
    (
        "skill:strategy-space",
        "Strategy (Space)",
        task(
            TACTICAL,
            "plan-space-campaign",
            "strategic-margin",
            resolution=R.QUICK_CONTEST,
            context=("opposing-command",),
            modifiers=COMMAND,
            margin=True,
        ),
    ),
    ("skill:sumo-wrestling", "Sumo Wrestling", unarmed("shove-grapple-or-resist")),
    (
        "skill:tactics",
        "Tactics",
        task(
            TACTICAL,
            "gain-tactical-advantage",
            "tactical-margin",
            resolution=R.QUICK_CONTEST,
            context=("opposing-force",),
            modifiers=COMMAND,
            margin=True,
        ),
    ),
    ("skill:tonfa", "Tonfa", weapon("strike-or-parry-with-tonfa")),
    (
        "skill:two-handed-axe-mace",
        "Two-Handed Axe/Mace",
        weapon("strike-with-two-handed-axe-or-mace"),
    ),
    ("skill:two-handed-flail", "Two-Handed Flail", weapon("strike-with-two-handed-flail")),
    ("skill:two-handed-sword", "Two-Handed Sword", weapon("strike-with-two-handed-sword")),
    ("skill:whip", "Whip", weapon("strike-or-entangle-with-whip")),
    ("skill:wrestling", "Wrestling", unarmed("grapple-or-resist")),
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return only concrete, dispatchable skills; family rows stay absent."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )


def _require_capability(capability_id: str) -> None:
    declared = capability(capability_id)
    if capability_id not in profile(PROFILE).required_capabilities:
        raise ValidationError(f"Rules capability outside profile: {capability_id}")
    if declared.status is CoverageStatus.ABSENT:
        raise ValidationError(f"Rules capability has no coverage: {capability_id}")


def _require_dispatchable(skill_id: str) -> object | None:
    procedure = PROCEDURES.get(skill_id)
    if procedure is None:
        return None
    if procedure.specialties:
        raise ValidationError(
            f"Combat skill family requires a concrete specialty: {skill_id}: "
            + ", ".join(procedure.specialties)
        )
    if not procedure.dispatchable:
        raise ValidationError(f"Combat skill has no item-specific procedure: {skill_id}")
    for capability_id in CAPABILITIES:
        _require_capability(capability_id)
    return procedure


def require_mode(
    profile_id: str,
    skill_id: str,
    *,
    ranged: bool,
    hands: int,
    parry: bool,
    fencing: bool,
    unbalanced: bool,
) -> object | None:
    """Fail closed when a Basic melee mode claims the wrong combat procedure."""
    if skill_id not in PROCEDURES:
        return None
    if profile_id != PROFILE:
        raise ValidationError(f"Combat skill requires the exact Basic Set profile: {skill_id}")
    procedure = _require_dispatchable(skill_id)
    weapon_class = WEAPON_CLASSES.get(skill_id)
    if ranged or weapon_class is None:
        raise ValidationError(f"Combat skill cannot resolve this melee weapon mode: {skill_id}")
    if hands not in weapon_class.hands:
        raise ValidationError(f"Weapon grip is outside the skill's class: {skill_id}")
    if parry and not weapon_class.permits_parry:
        raise ValidationError(f"Parry is outside the skill's class: {skill_id}")
    if weapon_class.fencing is not None and fencing != weapon_class.fencing:
        raise ValidationError(f"Fencing construction is outside the skill's class: {skill_id}")
    if unbalanced and not weapon_class.permits_unbalanced:
        raise ValidationError(f"Unbalanced construction is outside the skill's class: {skill_id}")
    return procedure


def require_shield(profile_id: str, skill_id: str) -> object | None:
    """Fail closed when a Basic shield names a family or non-shield skill."""
    if skill_id not in PROCEDURES:
        return None
    if profile_id != PROFILE:
        raise ValidationError(f"Combat skill requires the exact Basic Set profile: {skill_id}")
    # The pre-specialty equipment adapter used ``skill:shield`` for the B287
    # ordinary Shield specialty.  Preserve that wire value as an explicit
    # alias; authoring the family anywhere else still fails closed.
    effective_id = "skill:shield-standard" if skill_id == "skill:shield" else skill_id
    procedure = _require_dispatchable(effective_id)
    if effective_id not in {
        "skill:shield-standard",
        "skill:shield-buckler",
        "skill:shield-force",
    }:
        raise ValidationError(f"Combat skill cannot resolve this shield: {skill_id}")
    return procedure
