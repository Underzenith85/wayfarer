"""Representative higher-TL weapons and their ammunition."""

from decimal import Decimal

from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.simulation.equipment.basic.rows import ranged_weapon, source
from wayfarer.engine.simulation.equipment.catalog import (
    Damage,
    EquipmentProfile,
    RangedMode,
    RocketAcceleration,
    SmartgunSpec,
)

HIGHER_TL_WEAPONS = (
    ranged_weapon(
        "gyroc-pistol-15mm",
        278,
        9,
        200,
        600,
        RangedMode(
            id="shot",
            skill_id="skill:guns-gyroc",
            minimum_st=9,
            damage=Damage(basis="fixed", dice=6, damage_type="pi++"),
            accuracy=1,
            range_basis="yards",
            maximum_range=1900,
            rate_of_fire=3,
            shots=4,
            reload_seconds=3,
            reload_protocol="per-round",
            bulk=-2,
            recoil=1,
            ammunition_id="equipment:gyroc-pistol-15mm-round",
            firearm=FirearmSpec(
                technology_level=9,
                action="repeating",
                armoury_skill_id="skill:armoury-small-arms",
            ),
            rocket_acceleration=RocketAcceleration(
                close_max_yards=2,
                close_damage_divisor=3,
                medium_max_yards=10,
                medium_damage_divisor=2,
            ),
            smartgun=SmartgunSpec(),
        ),
    ),
    ranged_weapon(
        "laser-pistol",
        280,
        10,
        2800,
        2800,
        RangedMode(
            id="beam",
            skill_id="skill:beam-weapons-pistol",
            minimum_st=6,
            damage=Damage(
                basis="fixed",
                dice=3,
                damage_type="burn",
                armor_divisor=Decimal(2),
                tight_beam=True,
            ),
            accuracy=6,
            range_basis="yards",
            half_damage_range=250,
            maximum_range=750,
            rate_of_fire=10,
            shots=400,
            reload_seconds=3,
            bulk=-2,
            recoil=1,
            ammunition_id="equipment:laser-pistol-cell",
            firearm=FirearmSpec(
                technology_level=10,
                action="beam",
                armoury_skill_id="skill:armoury-small-arms",
            ),
            smartgun=SmartgunSpec(),
            beam_environment_dr=True,
        ),
    ),
)

HIGHER_TL_AMMUNITION = (
    EquipmentProfile(
        definition_id="equipment:gyroc-pistol-15mm-round",
        provenance=source(278),
        weight_millipounds=100,
        price=2,
        technology_level=9,
        ammunition=True,
    ),
    EquipmentProfile(
        definition_id="equipment:laser-pistol-cell",
        provenance=source(280),
        weight_millipounds=500,
        price=10,
        technology_level=10,
        ammunition=True,
        power_cell_capacity=400,
    ),
)
