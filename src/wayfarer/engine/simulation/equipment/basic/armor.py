"""Armor and shield rows (B283-287)."""

from typing import cast

from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.equipment.basic.rows import melee, source
from wayfarer.engine.simulation.equipment.catalog import Armor, EquipmentProfile, Location, Shield

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

_SHIELD_ROWS = (
    ("light", 0, 1, 25, 2000, 5, 20),
    ("small", 0, 1, 40, 8000, 6, 30),
    ("medium", 1, 2, 60, 15000, 7, 40),
    ("large", 1, 3, 90, 25000, 9, 60),
)


def _shield(
    identifier: str,
    tl: int,
    db: int,
    price: int,
    weight: int,
    dr: int,
    hp: int,
    *,
    skill: str = "shield-standard",
    buckler: bool = False,
    spiked: bool = False,
) -> EquipmentProfile:
    return EquipmentProfile(
        definition_id=f"equipment:{identifier}",
        provenance=source(287),
        technology_level=tl,
        weight_millipounds=weight,
        price=price,
        slot="shield",
        shield=Shield(
            skill_id=f"skill:{skill}",
            defense_bonus=db,
            buckler=buckler,
            can_rush=not buckler,
        ),
        modes=(
            melee(
                "shield-bash-spike" if spiked else "shield-bash",
                skill,
                None,
                "thrust",
                1 if spiked else 0,
                "cr",
                (1,),
                parry=None,
                shield_attack=True,
            ),
        ),
        durability=ObjectProfile(construction="homogenous", hp=hp, dr=dr, ht=12),
    )


# B287 notes 2-4: base shields, TL2 spikes, bucklers, TL3 iron, and TL7
# plastic-riot construction are distinct inventory profiles so their physical
# and procedural differences survive serialization and replay.
SHIELDS = (
    tuple(
        _shield(f"{name}-shield", tl, db, price, weight, dr, hp)
        for name, tl, db, price, weight, dr, hp in _SHIELD_ROWS
    )
    + tuple(
        _shield(
            f"spiked-{name}-shield", max(tl, 2), db, price + 20, weight + 5000, dr, hp, spiked=True
        )
        for name, tl, db, price, weight, dr, hp in _SHIELD_ROWS
    )
    + tuple(
        _shield(
            f"{name}-buckler",
            tl,
            db,
            price,
            weight,
            dr,
            hp,
            skill="shield-buckler",
            buckler=True,
        )
        for name, tl, db, price, weight, dr, hp in _SHIELD_ROWS[:3]
    )
    + tuple(
        _shield(f"iron-{name}-shield", max(tl, 3), db, price * 5, weight * 2, dr + 3, hp * 2)
        for name, tl, db, price, weight, dr, hp in _SHIELD_ROWS
    )
    + tuple(
        _shield(f"plastic-riot-{name}-shield", max(tl, 7), db, price, weight // 2, dr, hp)
        for name, tl, db, price, weight, dr, hp in _SHIELD_ROWS
    )
)
