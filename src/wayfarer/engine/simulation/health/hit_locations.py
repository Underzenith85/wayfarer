"""Human hit-location numeric rules; no fallback for undeclared anatomy.

Basic Set B378-379, B398-400, B420-422, B552. Optional cumulative wounds,
Injury Tolerance and nonhuman anatomy are intentionally not inferred.
"""

from collections.abc import Iterable
from decimal import Decimal

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import (
    HitLocation,
    HumanLocation,
    InjuryTolerance,
    disabled_locations,
)
from wayfarer.engine.simulation.equipment.catalog import Armor, DamageType
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def disabled(resources: ResourceState, actor_id: str) -> frozenset[HumanLocation]:
    """Locations this actor cannot use: crippled, still healing, or absent by anatomy."""
    hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    absent: frozenset[HumanLocation] = frozenset()
    if (
        hp.injury
        and hp.injury.tolerance
        and (hp.injury.tolerance.no_eyes or hp.injury.tolerance.no_head)
    ):
        absent = frozenset({"left-eye", "right-eye"})
    return (
        disabled_locations(
            hp.injury.lasting_injuries,
            now=resources.game_time,
            full_hp=hp.current >= hp.maximum,
        )
        if hp.injury
        else frozenset()
    ) | absent


FACTORS = {
    "cr": (1, 1),
    "cut": (3, 2),
    "imp": (2, 1),
    "pi-": (1, 2),
    "pi": (1, 1),
    "pi+": (3, 2),
    "pi++": (2, 1),
    "burn": (1, 1),
    "cor": (1, 1),
    "tox": (1, 1),
}
PENALTIES = {
    "torso": 0,
    "vitals": -3,
    "skull": -7,
    "face": -5,
    "neck": -5,
    "groin": -3,
    "arm": -2,
    "leg": -2,
    "hand": -4,
    "foot": -4,
    "eye": -9,
}


def torso_near_miss(location: HitLocation | None, check: CheckTrace) -> bool:
    """B552 note 1; automatic/critical failures never become location misses."""
    return (
        location in ("skull", "face", "left-eye", "right-eye", "groin", "neck", "vitals")
        and check.outcome is Outcome.FAILURE
        and check.margin == -1
        and check.total < 17
    )


def part(location: HumanLocation) -> str:
    return location.split("-", 1)[-1]


def require_location(status: InjuryStatus, location: HitLocation | None) -> None:
    if location is not None and (
        status.profile_id != "gurps-basic-set-4e-2004" or status.anatomy != "human"
    ):
        raise ValidationError("Hit locations require Basic Set and explicit living-human anatomy")
    if location is not None and location != "random" and missing_location(status, location):
        raise ValidationError("Cannot target a severed body part")


def missing_location(status: InjuryStatus, location: HumanLocation) -> bool:
    tolerance = status.tolerance
    if tolerance is not None and (
        tolerance.no_head
        and location in ("skull", "face", "left-eye", "right-eye")
        or tolerance.no_eyes
        and part(location) == "eye"
        or tolerance.no_neck
        and location == "neck"
    ):
        return True
    return any(
        w.kind == "severed"
        and (
            w.location == location
            or w.location.replace("arm", "hand").replace("leg", "foot") == location
        )
        for w in status.lasting_injuries
    )


def attack_penalty(location: HitLocation, *, shield_side: str | None = None) -> int:
    if location == "random":
        return 0
    penalty = PENALTIES[part(location)]
    return penalty * 2 if location in (f"{shield_side}-arm", f"{shield_side}-hand") else penalty


def select_location(
    location: HitLocation, *, rng: RandomSource, from_behind: bool = False
) -> tuple[HumanLocation, tuple[int, ...]]:
    if location != "random":
        return location, ()
    dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
    total = sum(dice)
    if total <= 4:
        return "skull", dice
    if total == 5:
        return "skull" if from_behind else "face", dice
    if total <= 7:
        return "right-leg", dice
    if total == 8:
        return "right-arm", dice
    if total <= 10:
        return "torso", dice
    if total == 11:
        return "groin", dice
    if total == 12:
        return "left-arm", dice
    if total <= 14:
        return "left-leg", dice
    if total in (15, 16):
        side = rng.randbelow(6) + 1
        if total == 15:
            return ("right-hand" if side <= 3 else "left-hand"), dice + (side,)
        return ("right-foot" if side <= 3 else "left-foot"), dice + (side,)
    return "neck", dice


def wound_factor(
    location: HumanLocation,
    damage_type: DamageType,
    *,
    tight_beam: bool,
    tolerance: InjuryTolerance | None = None,
) -> tuple[int, int]:
    if damage_type not in FACTORS:
        raise ValidationError("Unsupported location damage")
    piercing = damage_type.startswith("pi") or damage_type == "imp"
    if location == "vitals" or part(location) == "eye":
        if not piercing and not (damage_type == "burn" and tight_beam):
            raise ValidationError("Only penetrating or tight-beam attacks can target eyes/vitals")
    if damage_type == "tox":
        return FACTORS[damage_type]
    if tolerance is not None:
        if tolerance.structure in ("homogenous", "diffuse"):
            return tolerance_factor(tolerance, damage_type)
        if tolerance.no_brain and (location in ("skull", "face") or part(location) == "eye"):
            location = "torso" if part(location) != "eye" else "face"
        if tolerance.no_vitals and location in ("vitals", "groin"):
            location = "torso"
        if tolerance.structure == "unliving" and location not in (
            "skull",
            "vitals",
            "left-eye",
            "right-eye",
        ):
            return tolerance_factor(tolerance, damage_type)
    if location == "skull" or part(location) == "eye":
        return 4, 1
    if location == "vitals":
        return (3, 1) if piercing else (2, 1)
    if location == "neck":
        if damage_type == "cut":
            return 2, 1
        if damage_type in ("cr", "cor"):
            return 3, 2
    if location == "face" and damage_type == "cor":
        return 3, 2
    if part(location) in ("arm", "leg", "hand", "foot") and damage_type in ("imp", "pi+", "pi++"):
        return 1, 1
    return FACTORS[damage_type]


def tolerance_factor(tolerance: InjuryTolerance, damage_type: DamageType) -> tuple[int, int]:
    """B380/B552; diffuse single-blow injury is capped separately."""
    values = {
        "unliving": {"imp": (1, 1), "pi++": (1, 1), "pi+": (1, 2), "pi": (1, 3), "pi-": (1, 5)},
        "homogenous": {"imp": (1, 2), "pi++": (1, 2), "pi+": (1, 3), "pi": (1, 5), "pi-": (1, 10)},
    }
    return values.get(tolerance.structure, {}).get(damage_type, FACTORS[damage_type])


def location_special_effects(status: InjuryStatus, location: HumanLocation) -> bool:
    tolerance = status.tolerance
    return tolerance is None or not (
        tolerance.structure in ("homogenous", "diffuse")
        or tolerance.no_brain
        and (location in ("skull", "face") or part(location) == "eye")
        or tolerance.no_vitals
        and location in ("vitals", "groin")
    )


def effective_dr(
    resistance: int, divisor: Decimal, *, location: HumanLocation, damage_type: DamageType
) -> int:
    if not divisor.is_finite() or divisor <= 0:
        raise ValidationError("Armor divisor must be finite and positive")
    total = resistance + (2 if location == "skull" and damage_type != "tox" else 0)
    divided = int(Decimal(total) / divisor)
    return max(1, divided) if divisor < 1 else divided


def crippling_threshold(location: HumanLocation, maximum_hp: int) -> int | None:
    divisor = {"arm": 2, "leg": 2, "hand": 3, "foot": 3, "eye": 10}.get(part(location))
    return maximum_hp // divisor + 1 if divisor else None


def knockdown_penalty(location: HumanLocation, *, major: bool, male_groin: bool) -> int:
    if not major:
        return 0
    if location == "skull" or part(location) == "eye":
        return -10
    if location in ("face", "vitals") or location == "groin" and male_groin:
        return -5
    return 0


def armor_resistance(
    armors: Iterable[Armor], location: HumanLocation, *, rigid_only: bool = False
) -> int:
    """B282/B400: select covering armor before injury applies bone DR and divisors."""
    return max(
        (
            armor.dr
            for armor in armors
            if (location in armor.locations or part(location) + "s" in armor.locations)
            and not (rigid_only and armor.flexible)
        ),
        default=0,
    )
