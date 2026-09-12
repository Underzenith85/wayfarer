"""B280 ultra-tech index entries: facts without activation."""

from wayfarer.engine.simulation.equipment.basic.rows import source
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile

# B280: unresolved ultra-tech index entries carry facts but cannot be activated.
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
            ("linked-affliction", "surge"),
        ),
        ("equipment:blaster-pistol", 11, 2200, 1600, ("surge",)),
    )
)
