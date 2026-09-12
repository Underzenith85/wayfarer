"""Row constructors and vocabulary shared by every Basic Set listing.

Characters third printing (February 2008), B271-274, B283, B288-289. These are
explicit review data for the selected later-printing profile.
"""

from decimal import Decimal
from typing import Literal

from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.rules.types.readiness import ProjectileReadiness
from wayfarer.engine.simulation.equipment.catalog import (
    Damage,
    DamageType,
    EquipmentProfile,
    MeleeMode,
    MultipleProjectiles,
    Parry,
    Provenance,
    RangedMode,
    RatedStrength,
    TechnologyLevel,
)
from wayfarer.engine.simulation.equipment.objects import object_hp


def source(page: int | tuple[int, ...]) -> Provenance:
    return Provenance(
        source_id="sjg:basic-set-characters-4e-2004",
        edition="Fourth Edition, third printing (2008)",
        pages=(page,) if isinstance(page, int) else page,
        errata="Third-printing text; no separate errata overlay selected",
    )


def solid(weight: int, dr: int) -> ObjectProfile:
    return ObjectProfile(
        construction="homogenous", hp=object_hp(weight, "homogenous"), dr=dr, ht=12
    )


def melee(
    identifier: str,
    skill: str,
    minimum_st: int,
    basis: Literal["thrust", "swing", "fixed"],
    adds: int,
    damage_type: DamageType,
    reach: tuple[int, ...],
    *,
    hands: Literal[1, 2] = 1,
    parry: int | None = 0,
    unbalanced: bool = False,
    fencing: bool = False,
    divisor: str = "1",
    dice: int | None = None,
    ready_after_attack: bool = False,
) -> MeleeMode:
    """Construct one independently transcribed melee-table mode."""
    return MeleeMode(
        id=identifier,
        skill_id="skill:" + skill,
        minimum_st=minimum_st,
        hands=hands,
        damage=Damage(
            basis=basis,
            dice=dice,
            adds=adds,
            damage_type=damage_type,
            armor_divisor=Decimal(divisor),
        ),
        reach=reach,
        parry=(
            None if parry is None else Parry(modifier=parry, unbalanced=unbalanced, fencing=fencing)
        ),
        ready_after_attack=ready_after_attack,
    )


def weapon(
    identifier: str,
    page: int | tuple[int, ...],
    tl: TechnologyLevel,
    price: int,
    weight: int,
    *modes: MeleeMode,
    unsupported: tuple[str, ...] = (),
    durability_dr: int | None = None,
) -> EquipmentProfile:
    """Construct one physical row, merging alternate skill rows into its mode list."""
    return EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(page),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        slot="hand",
        durability=None if durability_dr is None else solid(weight, durability_dr),
        modes=modes,
        unsupported_mechanics=unsupported,
    )


def ranged(
    identifier: str,
    skill: str,
    minimum_st: int,
    basis: Literal["thrust", "swing", "fixed"],
    adds: int,
    damage_type: DamageType,
    accuracy: int,
    half_range: Decimal | int | None,
    maximum_range: Decimal | int,
    bulk: int,
    ammunition_id: str,
    *,
    dice: int | None = None,
    hands: Literal[1, 2] = 2,
    reload_seconds: int = 2,
    rated_kind: Literal["bow", "crossbow"] | None = None,
) -> RangedMode:
    """Construct one B275-276 muscle-powered launcher mode."""
    return RangedMode(
        id=identifier,
        skill_id="skill:" + skill,
        minimum_st=minimum_st,
        hands=hands,
        damage=Damage(
            basis=basis,
            dice=dice,
            adds=adds,
            damage_type=damage_type,
        ),
        accuracy=accuracy,
        range_basis="st",
        half_damage_range=half_range,
        maximum_range=maximum_range,
        shots=1,
        reload_seconds=reload_seconds,
        bulk=bulk,
        ammunition_id="equipment:" + ammunition_id,
        rated_strength=(
            None if rated_kind is None else RatedStrength(kind=rated_kind, st=minimum_st)
        ),
        readiness=(
            None
            if rated_kind is None
            else ProjectileReadiness(
                kind=rated_kind,
                fast_draw_skill_id="skill:fast-draw-arrow",
                fast_draw_specialty="Arrow",
            )
        ),
    )


def ranged_weapon(
    identifier: str,
    page: int,
    tl: int,
    price: Decimal | int,
    weight: int,
    *modes: RangedMode,
    unsupported: tuple[str, ...] = (),
) -> EquipmentProfile:
    return EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(page),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        slot="hand",
        modes=modes,
        unsupported_mechanics=unsupported,
    )


def firearm(
    identifier: str,
    skill: str,
    tl: int,
    damage_dice: int,
    damage_adds: int,
    damage_type: DamageType,
    accuracy: int,
    half_range: int,
    maximum_range: int,
    rate_of_fire: int,
    shots: int,
    reload_seconds: int,
    minimum_st: int,
    bulk: int,
    recoil: int,
    ammunition_id: str,
    action: Literal["muzzleloader", "breechloader", "revolver", "repeating"],
    *,
    hands: Literal[1, 2] = 1,
    reload_protocol: Literal["magazine", "per-round"] = "magazine",
    chamber_capacity: int = 0,
    projectiles_per_shot: int | None = None,
    minimum_shots_per_attack: int = 1,
) -> RangedMode:
    """Construct one independently transcribed B278-279 conventional-firearm row."""
    return RangedMode(
        id=identifier,
        skill_id="skill:guns-" + skill,
        minimum_st=minimum_st,
        hands=hands,
        damage=Damage(
            basis="fixed",
            dice=damage_dice,
            adds=damage_adds,
            damage_type=damage_type,
        ),
        accuracy=accuracy,
        range_basis="yards",
        half_damage_range=half_range,
        maximum_range=maximum_range,
        rate_of_fire=rate_of_fire,
        minimum_shots_per_attack=minimum_shots_per_attack,
        shots=shots,
        chamber_capacity=chamber_capacity,
        reload_seconds=reload_seconds,
        reload_protocol=reload_protocol,
        bulk=bulk,
        recoil=recoil,
        ammunition_id="equipment:" + ammunition_id,
        firearm=FirearmSpec(
            technology_level=tl,
            action=action,
            armoury_skill_id="skill:armoury-small-arms",
        ),
        multiple_projectiles=(
            None
            if projectiles_per_shot is None
            else MultipleProjectiles(projectiles_per_shot=projectiles_per_shot)
        ),
    )
