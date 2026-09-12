"""Armor and shield rows (B283-287)."""

from typing import cast

from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.equipment.basic.rows import source
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

# B287: ordinary shield rows. Durability uses the table's explicit DR/HP
# columns rather than deriving HP from weight. Cloaks are the same physical
# rows already recorded on B276; the superscience force shield cannot fit the
# integer TL or finite-HP schema and remains an explicit audit omission.
SHIELDS = tuple(
    EquipmentProfile(
        definition_id=identifier,
        provenance=source(287),
        technology_level=tl,
        weight_millipounds=weight,
        price=price,
        slot="shield",
        shield=Shield(skill_id="skill:shield", defense_bonus=db),
        durability=ObjectProfile(construction="homogenous", hp=hp, dr=dr, ht=12),
    )
    for identifier, tl, db, price, weight, dr, hp in (
        ("equipment:light-shield", 0, 1, 25, 2000, 5, 20),
        ("equipment:small-shield", 0, 1, 40, 8000, 6, 30),
        ("equipment:medium-shield", 1, 2, 60, 15000, 7, 40),
        ("equipment:large-shield", 1, 3, 90, 25000, 9, 60),
    )
)
