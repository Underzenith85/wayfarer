"""Ammunition rows for firearms, long guns and shotguns."""

from decimal import Decimal
from fractions import Fraction

from wayfarer.engine.simulation.equipment.basic.rows import source
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile

FIREARM_AMMUNITION = tuple(
    EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(278),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        ammunition=True,
    )
    for identifier, tl, price, weight in (
        ("flintlock-pistol-51-round", 4, Decimal("0.2"), 10),
        ("wheel-lock-pistol-60-round", 4, Decimal("0.2"), 10),
        ("derringer-41-round", 5, 1, 50),
        ("revolver-36-round", 5, Decimal("0.8"), 40),
        ("snub-revolver-38-round", 6, Decimal("0.8"), 40),
        ("auto-pistol-45-tl6-round", 6, Decimal("1.5"), 75),
        ("auto-pistol-9mm-tl6-round", 6, Fraction(8, 9), Fraction(400, 9)),
        ("revolver-38-round", 6, Fraction(2, 3), Fraction(100, 3)),
        ("auto-pistol-9mm-tl7-round", 7, Decimal("0.8"), 40),
        ("holdout-pistol-380-round", 7, Decimal("0.8"), 40),
        ("revolver-357m-round", 7, Decimal("0.7"), 35),
        ("revolver-44m-round", 7, 1, 50),
        ("auto-pistol-44m-round", 8, Fraction(4, 3), Fraction(200, 3)),
        ("auto-pistol-40-round", 8, Fraction(14, 15), Fraction(140, 3)),
        ("machine-pistol-9mm-round", 7, Fraction(22, 25), 44),
        ("smg-9mm-tl6-round", 6, Fraction(15, 16), Fraction(375, 8)),
        ("smg-45-round", 6, Fraction(49, 25), 98),
        ("smg-9mm-tl7-round", 7, Decimal("0.8"), 40),
        ("pdw-46-round", 8, Decimal("0.5"), 25),
    )
)

LONG_GUN_AMMUNITION = tuple(
    EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(279),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        ammunition=True,
    )
    for identifier, tl, price, weight in (
        ("handgonne-90-round", 3, 2, 100),
        ("flintlock-musket-75-round", 4, 1, 50),
        ("rifle-musket-577-round", 5, 1, 50),
        ("cartridge-rifle-45-round", 5, 2, 100),
        ("lever-action-carbine-30-round", 5, 1, 50),
        ("bolt-action-rifle-762-round", 6, Fraction(6, 5), 60),
        ("self-loading-rifle-762-round", 6, Fraction(5, 4), Fraction(125, 2)),
        ("assault-rifle-556-round", 7, Fraction(2, 3), Fraction(100, 3)),
        ("assault-rifle-762s-round", 7, Fraction(6, 5), 60),
        ("battle-rifle-762-round", 7, Fraction(17, 10), 85),
        ("assault-carbine-556-round", 8, Fraction(2, 3), Fraction(100, 3)),
    )
)

SHOTGUN_AMMUNITION = tuple(
    EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(279),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        ammunition=True,
    )
    for identifier, tl, price, weight in (
        ("blunderbuss-8g-round", 4, Decimal("2.6"), 130),
        ("double-shotgun-10g-round", 5, 1, 50),
        ("pump-shotgun-12g-round", 6, Decimal("2.8"), 140),
        ("auto-shotgun-12g-round", 7, Fraction(17, 7), Fraction(850, 7)),
    )
)
