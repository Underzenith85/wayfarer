"""Muscle-powered ranged weapons and their ammunition (B275-276)."""

from decimal import Decimal

from wayfarer.engine.simulation.equipment.basic.rows import ranged, ranged_weapon, source
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile

# B275-276. The table's slash-separated weights are split into the reusable
# launcher and one separately inventoried missile. Rows whose damage is
# ``spec.`` remain indexed and unavailable until their binding procedure can be
# stated without inventing a damage value.
MUSCLE_POWERED_RANGED = (
    ranged_weapon(
        "blowpipe",
        275,
        0,
        30,
        1000,
        ranged("shot", "blowpipe", 2, "fixed", -3, "pi-", 1, None, 4, -6, "blowpipe-dart", dice=1),
        unsupported=("follow-up-poison-or-drug",),
    ),
    ranged_weapon(
        "longbow",
        275,
        0,
        200,
        3000,
        ranged("shot", "bow", 11, "thrust", 2, "imp", 3, 15, 20, -8, "arrow", rated_kind="bow"),
    ),
    ranged_weapon(
        "regular-bow",
        275,
        0,
        100,
        2000,
        ranged("shot", "bow", 10, "thrust", 1, "imp", 2, 15, 20, -7, "arrow", rated_kind="bow"),
    ),
    ranged_weapon(
        "short-bow",
        275,
        0,
        50,
        2000,
        ranged("shot", "bow", 7, "thrust", 0, "imp", 1, 10, 15, -6, "arrow", rated_kind="bow"),
    ),
    ranged_weapon(
        "composite-bow",
        275,
        1,
        900,
        4000,
        ranged("shot", "bow", 10, "thrust", 3, "imp", 3, 20, 25, -7, "arrow", rated_kind="bow"),
    ),
    ranged_weapon(
        "crossbow",
        276,
        2,
        150,
        6000,
        ranged(
            "shot",
            "crossbow",
            7,
            "thrust",
            4,
            "imp",
            4,
            20,
            25,
            -6,
            "bolt",
            reload_seconds=4,
            rated_kind="crossbow",
        ),
    ),
    ranged_weapon(
        "pistol-crossbow",
        276,
        3,
        150,
        4000,
        ranged(
            "shot",
            "crossbow",
            7,
            "thrust",
            2,
            "imp",
            1,
            15,
            20,
            -4,
            "bolt",
            reload_seconds=4,
            rated_kind="crossbow",
        ),
        unsupported=("one-handed-rated-crossbow",),
    ),
    ranged_weapon(
        "prodd",
        276,
        3,
        150,
        6000,
        ranged(
            "shot",
            "crossbow",
            7,
            "thrust",
            4,
            "pi",
            2,
            20,
            25,
            -6,
            "lead-pellet",
            reload_seconds=4,
            rated_kind="crossbow",
        ),
    ),
    ranged_weapon(
        "sling",
        276,
        0,
        20,
        500,
        ranged("shot", "sling", 6, "swing", 0, "pi", 0, 6, 10, -4, "sling-stone"),
    ),
    ranged_weapon(
        "staff-sling",
        276,
        1,
        20,
        2000,
        ranged("shot", "sling", 7, "swing", 1, "pi", 1, 10, 15, -6, "sling-stone"),
    ),
    ranged_weapon("bolas", 275, 0, 20, 2000, unsupported=("entangling-special-damage",)),
    ranged_weapon("heavy-cloak", 275, 1, 50, 5000, unsupported=("entangling-special-damage",)),
    ranged_weapon("light-cloak", 275, 1, 20, 2000, unsupported=("entangling-special-damage",)),
    ranged_weapon("lariat", 276, 1, 40, 3000, unsupported=("entangling-special-damage",)),
    ranged_weapon("large-net", 276, 0, 40, 20000, unsupported=("entangling-special-damage",)),
    ranged_weapon("melee-net", 276, 2, 20, 5000, unsupported=("entangling-special-damage",)),
    ranged_weapon("atlatl", 276, 0, 20, 1000, unsupported=("launcher-assisted-throw",)),
    ranged_weapon("goats-foot", 276, 3, 50, 2000, unsupported=("crossbow-cocking-aid",)),
)

MUSCLE_POWERED_AMMUNITION = tuple(
    EquipmentProfile(
        definition_id="equipment:" + identifier,
        provenance=source(276),
        weight_millipounds=weight,
        price=price,
        technology_level=tl,
        ammunition=True,
    )
    for identifier, tl, price, weight in (
        ("blowpipe-dart", 0, Decimal("0.1"), 50),
        ("arrow", 0, 2, 100),
        ("bolt", 2, 2, 60),
        ("lead-pellet", 2, Decimal("0.1"), 60),
        ("sling-stone", 0, 0, 50),
    )
)
