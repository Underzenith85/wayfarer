"""Audited numeric equipment selection; no rulebook prose or automatic activation.

Characters third printing (February 2008), B271, B283, B288-289. These are
explicit review data for the first-printing profile, not first-printing proof.
"""

from typing import cast

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record
from wayfarer.rules.object_types import ObjectProfile
from wayfarer.simulation.gurps_equipment import (
    Armor,
    Damage,
    DamageType,
    EquipmentCatalog,
    EquipmentProfile,
    Location,
    MeleeMode,
    Parry,
    Provenance,
)
from wayfarer.simulation.objects import object_hp


def source(page: int) -> Provenance:
    return Provenance(
        source_id="sjg:gurps-basic-set-4e-2004",
        edition="Fourth Edition, third printing (2008)",
        pages=(page,),
        errata="Third-printing text; no separate errata overlay; first-printing audit pending",
    )


def solid(weight: int, dr: int) -> ObjectProfile:
    return ObjectProfile(
        construction="homogenous", hp=object_hp(weight, "homogenous"), dr=dr, ht=12
    )


# B271: the two complete, no-special-footnote Broadsword rows selected for this audit.
WEAPONS = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(271),
        weight_millipounds=3000,
        price=price,
        technology_level=tl,
        slot="hand",
        durability=solid(3000, dr),
        modes=(
            MeleeMode(
                id="swing",
                skill_id="skill:broadsword",
                minimum_st=10,
                damage=Damage(basis="swing", adds=1, damage_type=cast(DamageType, kind)),
                reach=(1,),
                parry=Parry(),
            ),
            MeleeMode(
                id="thrust",
                skill_id="skill:broadsword",
                minimum_st=10,
                damage=Damage(basis="thrust", adds=1, damage_type="cr"),
                reach=(1,),
                parry=Parry(),
            ),
        ),
    )
    for identifier, tl, price, dr, kind in (
        ("equipment:light-club", 0, 5, 2, "cr"),
        ("equipment:broadsword", 2, 500, 6, "cut"),
    )
)

# B283: complete rigid, unsplit body-armor rows without special footnotes.
ARMOR = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(283),
        technology_level=tl,
        weight_millipounds=weight,
        price=price,
        slot="body",
        armor=Armor(locations=cast(tuple[Location, ...], locations), dr=dr),
    )
    for identifier, tl, price, weight, locations, dr in (
        ("equipment:bronze-corselet", 1, 1300, 40000, ("torso", "groin"), 5),
        ("equipment:leather-armor", 1, 100, 10000, ("torso", "groin"), 2),
        ("equipment:light-scale-armor", 2, 150, 15000, ("torso",), 3),
        ("equipment:lorica-segmentata", 2, 680, 26000, ("torso",), 5),
        ("equipment:scale-armor", 2, 420, 35000, ("torso", "groin"), 4),
        ("equipment:heavy-steel-corselet", 3, 2300, 45000, ("torso", "groin"), 7),
        ("equipment:steel-corselet", 3, 1300, 35000, ("torso", "groin"), 6),
        ("equipment:steel-laminate-plate", 3, 900, 30000, ("torso", "groin"), 5),
    )
)

# B288: inventory facts. Behavioral requirements keep special tools out of active
# packages until their mechanics are integrated; no invented skill/effect hooks.
ORDINARY = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(288),
        technology_level=tl,
        price=price,
        weight_millipounds=weight,
        container_capacity_millipounds=capacity,
        unsupported_mechanics=missing,
    )
    for identifier, tl, price, weight, capacity, missing in (
        ("equipment:frame-backpack", 1, 100, 10000, 100000, ()),
        ("equipment:small-backpack", 1, 60, 3000, 40000, ()),
        ("equipment:blanket", 1, 20, 4000, None, ()),
        ("equipment:six-foot-pole", 0, 5, 3000, None, ()),
        ("equipment:ten-foot-pole", 0, 8, 5000, None, ()),
        ("equipment:scribes-kit", 3, 50, 2000, None, ()),
        ("equipment:canteen", 5, 10, 1000, 2000, ()),
        ("equipment:camp-stove", 6, 50, 2000, None, ("fuel-consumption",)),
        ("equipment:insulated-sleeping-bag", 7, 100, 15000, None, ("cold-resistance-bonus",)),
        ("equipment:laptop", 8, 1500, 3000, None, ("battery-and-computer-operation",)),
    )
)

BASIC_EQUIPMENT = EquipmentCatalog(
    profile_id="gurps-basic-set-4e-2004",
    entries=WEAPONS + ARMOR + ORDINARY,
)

# B280: ultra-tech index entries carry facts but cannot be activated as ordinary
# equipment. Cell charge accounting, smartguns, and environmental beam DR are
# not interchangeable with the existing per-round ammunition implementation.
ULTRATECH_INDEX = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(280),
        technology_level=tl,
        price=price,
        weight_millipounds=weight,
        unsupported_mechanics=missing,
    )
    for identifier, tl, price, weight, missing in (
        (
            "equipment:electrolaser-pistol",
            9,
            1800,
            2200,
            ("power-cell-charges", "smartgun", "linked-affliction", "surge", "beam-environment"),
        ),
        (
            "equipment:laser-pistol",
            10,
            2800,
            3300,
            ("power-cell-charges", "smartgun", "beam-environment"),
        ),
        ("equipment:blaster-pistol", 11, 2200, 1600, ("power-cell-charges", "smartgun", "surge")),
    )
)

# Vehicle listing is deliberately separate from wearable/carryable equipment.
# Campaigns fourth printing B464; masses are loaded tons, never inventory lbs.


class VehicleEntry(Record):
    definition_id: Id
    page: int = 464
    edition: str = "Campaigns Fourth Edition, fourth printing"
    technology_level: int = Field(ge=0)
    hp: int = Field(gt=0)
    dr: int = Field(ge=0)
    price: int = Field(ge=0)
    required_capabilities: tuple[str, ...] = ("gurps.vehicles.movement", "gurps.vehicles.combat")

    def require_operation(self) -> None:
        # #120 closed without the operation integration; #358 owns the two
        # capability rows a listed vehicle would need before it can be driven.
        raise ValidationError("Vehicle listing does not implement operation; see #358")


VEHICLE_INDEX = (
    VehicleEntry(definition_id="vehicle:wagon", technology_level=3, hp=35, dr=2, price=680),
    VehicleEntry(definition_id="vehicle:luxury-car", technology_level=8, hp=57, dr=5, price=30000),
)
