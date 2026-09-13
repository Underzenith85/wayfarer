"""Pure Basic Set special-melee procedures (Campaigns B400-B406).

These functions accept declared facts, never caller-computed modifiers.  Stateful
combat dispatch remains in ``simulation.combat``; this module owns the compact
tables shared by armed, unarmed, geometry, and adjudication handlers.
"""

from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.errors import ValidationError

DamageType = Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn", "cor", "tox", "fat"]
ChinkDamage = Literal["imp", "pi-", "pi", "pi+", "pi++", "burn"]
DirtyTrickMode = Literal["attacker", "victim", "quick-contest"]
SpecialUnarmed = Literal[
    "elbow-strike",
    "knee-strike",
    "lethal-strike",
    "neck-snap",
    "wrench-limb",
    "trample",
]


def chink_penalty(
    location: HitLocation | None, damage_type: DamageType, *, tight_beam: bool
) -> int:
    """B400: derive the complete chink penalty from the declared target and attack."""
    if location == "random":
        raise ValidationError("Armor chinks require a declared hit location")
    if damage_type not in ("imp", "pi-", "pi", "pi+", "pi++") and not (
        damage_type == "burn" and tight_beam
    ):
        raise ValidationError("Only piercing, impaling, or tight-beam attacks target chinks")
    return -8 if location in (None, "torso") else -10


_REACH_EXTENSION = {1: 0, 2: 1, 3: 2, 4: 3, 5: 5, 6: 7, 7: 10, 8: 15, 9: 20, 10: 30}


def size_reach(reaches: tuple[int, ...], size_modifier: int) -> tuple[int, ...]:
    """B402: positive SM changes only a weapon's upper reach; SM +1 only extends C."""
    if not reaches or tuple(sorted(set(reaches))) != reaches:
        raise ValidationError("Size reach requires unique ascending reaches")
    if not -10 <= size_modifier <= 10:
        raise ValidationError("Size Modifier is outside the supported Basic Set table")
    if size_modifier <= 0:
        return reaches
    if size_modifier == 1:
        return (0, 1) if reaches == (0,) else reaches
    extension = _REACH_EXTENSION[size_modifier]
    upper = reaches[-1] + extension
    return tuple(range(reaches[0], upper + 1))


def grapple_size_bonus(attacker_sm: int, defender_sm: int) -> int:
    """B402: +1 to a grapple attack per point of positive relative SM."""
    return max(0, attacker_sm - defender_sm)


@dataclass(frozen=True, slots=True)
class AboveAttack:
    vision_modifier: int
    defense_modifier: int | None
    attack_modifier: int
    falling_damage: bool


def attack_from_above(*, looking_up: bool, stealth_won: bool, drop_yards: int) -> AboveAttack:
    """B402 declaration consequences after the ordinary Stealth/Vision contest."""
    if type(drop_yards) is not int or drop_yards < 0:
        raise ValidationError("Drop height must be a nonnegative whole number of yards")
    return AboveAttack(
        vision_modifier=2 if looking_up else -2,
        defense_modifier=None if stealth_won else -2,
        attack_modifier=-2,
        falling_damage=drop_yards > 2,
    )


@dataclass(frozen=True, slots=True)
class UnarmedTechnique:
    attack_modifier: int
    reach: tuple[int, ...]
    damage_add: int = 0
    damage_type: DamageType = "cr"
    defense_modifier: int = 0
    contest_st_modifier: int = 0
    large_area: bool = False


def special_unarmed(
    technique: SpecialUnarmed,
    *,
    location: HitLocation | None = None,
    front_grapple: bool = False,
    target_prone: bool = False,
    attacker_sm: int = 0,
    defender_sm: int = 0,
) -> UnarmedTechnique:
    """B403-B404 technique-specific values derived from the declared situation."""
    if technique == "elbow-strike":
        return UnarmedTechnique(-2 - int(location not in (None, "torso")), (0,))
    if technique == "knee-strike":
        return UnarmedTechnique(
            -1,
            (0,),
            defense_modifier=-2 if front_grapple else 0,
        )
    if technique == "lethal-strike":
        if location not in (None, "torso", "vitals", "left-eye", "right-eye"):
            raise ValidationError("Lethal Strike only gains special access to vitals or eyes")
        return UnarmedTechnique(-2, (0,), damage_add=-1, damage_type="pi")
    if technique in ("neck-snap", "wrench-limb"):
        expected = "neck" if technique == "neck-snap" else ("arm", "leg")
        part = (location or "").split("-", 1)[-1]
        if part not in ((expected,) if isinstance(expected, str) else expected):
            raise ValidationError("Technique requires a prior grapple of its named body part")
        return UnarmedTechnique(0, (0,), contest_st_modifier=-4)
    if attacker_sm < defender_sm + (1 if target_prone else 2):
        raise ValidationError("Trampling requires sufficient relative Size Modifier")
    return UnarmedTechnique(
        0,
        (0,),
        large_area=attacker_sm >= defender_sm + 3,
    )


@dataclass(frozen=True, slots=True)
class ImprovisedWeapon:
    attack_modifier: int
    parry_modifier: int
    minimum_st_add: int
    damage_add: int


def improvised_weapon(
    *, clumsiness: int, short_or_light: bool = False, weak_edge: bool = False
) -> ImprovisedWeapon:
    """B404: authoritative GM classification, bounded to the printed adjustments."""
    if clumsiness not in (0, 1, 2, 3):
        raise ValidationError("Improvised clumsiness must be classified from zero through three")
    reduction = int(short_or_light) + int(weak_edge)
    return ImprovisedWeapon(-clumsiness, -clumsiness, clumsiness, -reduction)


@dataclass(frozen=True, slots=True)
class DirtyTrick:
    attacker_modifier: int
    victim_modifier: int
    mode: DirtyTrickMode


def dirty_trick(
    *, novelty: Literal["new", "repeated", "known"], mode: DirtyTrickMode
) -> DirtyTrick:
    """B405: retain GM choice while preventing a known trick from granting mechanics."""
    if novelty == "known":
        raise ValidationError("A known dirty trick has no repeatable combat effect")
    penalty = 0 if novelty == "new" else -2
    return DirtyTrick(penalty, 0, mode)


def liquid_in_face(*, critical_hit: bool, defended: bool, will_succeeded: bool) -> tuple[int, int]:
    """B405: seconds blinded, then defense/DX-or-Sense penalty."""
    if critical_hit:
        return 1, 0  # Duration is the engine-recorded 1d result.
    return (0, -2) if not defended and not will_succeeded else (0, 0)


def special_weapon_defense(kind: Literal["fencing", "flail", "whip", "kusari"]) -> tuple[int, int]:
    """B404-B406: parry/block adjustments before ordinary defense scoring."""
    return {
        "fencing": (0, 0),
        "flail": (-4, -2),
        "whip": (-2, 0),
        "kusari": (-4, -2),
    }[kind]
