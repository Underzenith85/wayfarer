"""Independent selected-row audit: Characters third printing B271-288."""

from fractions import Fraction

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.simulation.basic_equipment import BASIC_EQUIPMENT, ULTRATECH_INDEX, VEHICLE_INDEX
from wayfarer.simulation.gurps_equipment import EquipmentCatalog, MeleeMode, RangedMode

MELEE_ROWS = (
    ("axe", 271, 0, 50, 4000),
    ("hatchet", 271, 0, 40, 2000),
    ("throwing-axe", 271, 0, 60, 4000),
    ("mace", 271, 2, 50, 5000),
    ("small-mace", 271, 2, 35, 3000),
    ("pick", 271, 3, 70, 3000),
    ("blackjack", 271, 1, 20, 1000),
    ("light-club", 271, 0, 5, 3000),
    ("broadsword", 271, 2, 500, 3000),
    ("thrusting-broadsword", 271, 2, 600, 3000),
    ("bastard-sword", 271, 3, 650, 5000),
    ("katana", 271, 3, 650, 5000),
    ("thrusting-bastard-sword", 271, 3, 750, 5000),
    ("cavalry-saber", 271, 4, 500, 3000),
    ("morningstar", 272, 3, 80, 6000),
    ("nunchaku", 272, 3, 20, 2000),
    ("large-knife", 272, 0, 40, 1000),
    ("small-knife", 272, 0, 30, 500),
    ("wooden-stake", 272, 0, 4, 500),
    ("dagger", 272, 1, 20, 250),
    ("kusari", 272, 3, 70, 5000),
    ("lance", 272, 2, 60, 6000),
    ("glaive", 272, 1, 100, 8000),
    ("naginata", 272, 2, 100, 6000),
    ("halberd", 272, 3, 150, 12000),
    ("poleaxe", 272, 3, 120, 10000),
    ("rapier", 273, 4, 500, 2750),
    ("saber", 273, 4, 700, 2000),
    ("baton", 273, 0, 20, 1000),
    ("shortsword", 273, 2, 400, 2000),
    ("cutlass", 273, 4, 300, 2000),
    ("cattle-prod", 273, 7, 50, 2000),
    ("short-staff", 273, 0, 20, 1000),
    ("smallsword", 273, 4, 400, 1500),
    ("spear", 273, 0, 40, 4000),
    ("javelin", 273, 1, 30, 2000),
    ("long-spear", 273, 2, 60, 5000),
    ("quarterstaff", 273, 0, 10, 4000),
    ("maul", 274, 0, 80, 12000),
    ("great-axe", 274, 1, 100, 8000),
    ("scythe", 274, 1, 15, 5000),
    ("warhammer", 274, 3, 100, 7000),
    ("flail", 274, 2, 100, 8000),
    ("greatsword", 274, 3, 800, 7000),
    ("thrusting-greatsword", 274, 3, 900, 7000),
)

RANGED_ROWS = (
    ("blowpipe", 275, 0, 30, 1000, 2, 1, None, 4, -6),
    ("longbow", 275, 0, 200, 3000, 11, 3, 15, 20, -8),
    ("regular-bow", 275, 0, 100, 2000, 10, 2, 15, 20, -7),
    ("short-bow", 275, 0, 50, 2000, 7, 1, 10, 15, -6),
    ("composite-bow", 275, 1, 900, 4000, 10, 3, 20, 25, -7),
    ("crossbow", 276, 2, 150, 6000, 7, 4, 20, 25, -6),
    ("pistol-crossbow", 276, 3, 150, 4000, 7, 1, 15, 20, -4),
    ("prodd", 276, 3, 150, 6000, 7, 2, 20, 25, -6),
    ("sling", 276, 0, 20, 500, 6, 0, 6, 10, -4),
    ("staff-sling", 276, 1, 20, 2000, 7, 1, 10, 15, -6),
)

FIREARM_ROWS = (
    ("flintlock-pistol-51", 4, 200, 3000, 2, -1, "pi+", 1, 75, 450, 1, 1, 20, 10, -3, 2),
    ("wheel-lock-pistol-60", 4, 200, 3250, 1, 1, "pi+", 1, 75, 400, 1, 1, 20, 10, -3, 2),
    ("derringer-41", 5, 100, 500, 1, 0, "pi+", 1, 80, 650, 1, 2, 3, 9, -1, 2),
    ("revolver-36", 5, 150, 2500, 2, -1, "pi", 1, 120, 1300, 1, 6, 3, 10, -2, 2),
    ("snub-revolver-38", 6, 250, 1500, 1, 2, "pi", 1, 120, 1250, 3, 5, 3, 8, -1, 3),
    ("auto-pistol-45-tl6", 6, 300, 3000, 2, 0, "pi+", 2, 175, 1700, 3, 8, 3, 10, -2, 3),
    ("auto-pistol-9mm-tl6", 6, 350, 2400, 2, 2, "pi", 2, 150, 1850, 3, 9, 3, 9, -2, 2),
    ("smg-9mm-tl6", 6, 700, 10500, 3, -1, "pi", 3, 160, 1900, 8, 32, 3, 10, -4, 2),
    ("blunderbuss-8g", 4, 150, 12000, 1, 0, "pi", 1, 15, 100, 1, 1, 15, 11, -5, 1),
    ("double-shotgun-10g", 5, 450, 10000, 1, 2, "pi", 3, 50, 125, 2, 2, 3, 11, -5, 1),
    ("pump-shotgun-12g", 6, 240, 8000, 1, 1, "pi", 3, 50, 125, 2, 5, 3, 10, -5, 1),
    ("auto-shotgun-12g", 7, 950, 8400, 1, 1, "pi", 3, 50, 125, 3, 7, 3, 10, -5, 1),
)

LONG_GUN_ROWS = (
    ("handgonne-90", 3, 300, 15000, 2, 0, "pi++", 0, 100, 600, 60, 10, -6, 4),
    ("flintlock-musket-75", 4, 200, 13000, 4, 0, "pi++", 2, 100, 1500, 15, 10, -6, 4),
    ("rifle-musket-577", 5, 150, 8500, 4, 0, "pi+", 4, 700, 2100, 15, 10, -6, 3),
    ("cartridge-rifle-45", 5, 200, 6000, 5, 0, "pi+", 3, 600, 2000, 4, 10, -6, 3),
)

REPEATING_RIFLE_ROWS = (
    ("lever-action-carbine-30", 5, 300, 7000, 5, 0, 4, 450, 3000, 1, 6, 1, 3, 10, -4, 2),
    ("bolt-action-rifle-762", 6, 350, 8900, 7, 0, 5, 1000, 4200, 1, 5, 1, 3, 10, -5, 4),
    ("self-loading-rifle-762", 6, 600, 10000, 7, 0, 5, 1000, 4200, 3, 8, 0, 3, 10, -5, 3),
    ("assault-rifle-556", 7, 800, 9000, 5, 0, 5, 500, 3500, 12, 30, 1, 3, 9, -4, 2),
    ("assault-rifle-762s", 7, 300, 10500, 5, 1, 4, 400, 3000, 10, 30, 1, 3, 10, -4, 2),
    ("battle-rifle-762", 7, 900, 11000, 7, 0, 5, 1000, 4200, 11, 20, 1, 3, 11, -5, 3),
    ("assault-carbine-556", 8, 900, 7300, 4, 2, 4, 400, 3000, 15, 30, 1, 3, 9, -3, 2),
)

ORDINARY_HANDGUN_ROWS = (
    ("revolver-38", "pistol", 6, 400, 2000, 2, -1, "pi", 2, 120, 1500, 3, 6, 0, 3, 8, -2, 2),
    (
        "auto-pistol-9mm-tl7",
        "pistol",
        7,
        600,
        2600,
        2,
        2,
        "pi",
        2,
        150,
        1850,
        3,
        15,
        1,
        3,
        9,
        -2,
        2,
    ),
    (
        "holdout-pistol-380",
        "pistol",
        7,
        300,
        1300,
        2,
        0,
        "pi",
        1,
        125,
        1500,
        3,
        5,
        1,
        3,
        8,
        -1,
        3,
    ),
    ("revolver-357m", "pistol", 7, 500, 3000, 3, -1, "pi", 2, 185, 2000, 3, 6, 0, 3, 10, -2, 3),
    ("revolver-44m", "pistol", 7, 900, 3250, 3, 0, "pi+", 2, 200, 2500, 3, 6, 0, 3, 11, -3, 4),
    (
        "auto-pistol-44m",
        "pistol",
        8,
        750,
        4500,
        3,
        0,
        "pi+",
        2,
        230,
        2500,
        3,
        9,
        1,
        3,
        12,
        -3,
        4,
    ),
    ("auto-pistol-40", "pistol", 8, 640, 2100, 2, 0, "pi+", 2, 150, 1900, 3, 15, 1, 3, 9, -2, 2),
    ("machine-pistol-9mm", "smg", 7, 900, 5500, 2, 2, "pi", 2, 160, 1900, 20, 25, 1, 3, 12, -3, 3),
)

SHIELD_ROWS = (
    ("light-shield", 0, 1, 25, 2000, 5, 20),
    ("small-shield", 0, 1, 40, 8000, 6, 30),
    ("medium-shield", 1, 2, 60, 15000, 7, 40),
    ("large-shield", 1, 3, 90, 25000, 9, 60),
)


@pytest.mark.parametrize(
    "key,tl,cost,weight",
    [
        ("light-club", 0, 5, 3000),
        ("broadsword", 2, 500, 3000),
        ("bronze-corselet", 1, 1300, 40000),
        ("leather-armor", 1, 100, 10000),
        ("light-scale-armor", 2, 150, 15000),
        ("lorica-segmentata", 2, 680, 26000),
        ("scale-armor", 2, 420, 35000),
        ("heavy-steel-corselet", 3, 2300, 45000),
        ("steel-corselet", 3, 1300, 35000),
        ("steel-laminate-plate", 3, 900, 30000),
        ("frame-backpack", 1, 100, 10000),
        ("small-backpack", 1, 60, 3000),
        ("blanket", 1, 20, 4000),
        ("six-foot-pole", 0, 5, 3000),
        ("ten-foot-pole", 0, 8, 5000),
        ("scribes-kit", 3, 50, 2000),
        ("canteen", 5, 10, 1000),
        ("camp-stove", 6, 50, 2000),
        ("insulated-sleeping-bag", 7, 100, 15000),
        ("laptop", 8, 1500, 3000),
        ("electrolaser-pistol", 9, 1800, 2200),
        ("laser-pistol", 10, 2800, 3300),
        ("blaster-pistol", 11, 2200, 1600),
    ],
)
def test_selected_source_rows(key: str, tl: int, cost: int, weight: int) -> None:
    entry = next(
        e
        for e in BASIC_EQUIPMENT.entries + ULTRATECH_INDEX
        if e.definition_id == "equipment:" + key
    )
    assert (entry.technology_level, entry.price, entry.weight_millipounds) == (tl, cost, weight)
    assert entry.provenance.edition == "Fourth Edition, third printing (2008)"


def test_b271_274_melee_rows_have_independent_inventory_facts() -> None:
    actual = {
        (
            entry.definition_id.removeprefix("equipment:"),
            entry.provenance.pages[0],
            entry.technology_level,
            entry.price,
            entry.weight_millipounds,
        )
        for entry in BASIC_EQUIPMENT.entries
        if entry.provenance.pages[0] in (271, 272, 273, 274)
    }
    assert actual == set(MELEE_ROWS)
    pages = {
        entry.definition_id: entry.provenance.pages
        for entry in BASIC_EQUIPMENT.entries
        if entry.provenance.pages[0] in (271, 272, 273, 274)
    }
    assert pages["equipment:bastard-sword"] == (271, 274)
    assert pages["equipment:naginata"] == (272, 273, 274)
    assert pages["equipment:quarterstaff"] == (273, 274)


def test_melee_table_modes_cover_parry_hands_and_footnotes() -> None:
    entries = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}

    knife = entries["equipment:large-knife"]
    knife_modes = [mode for mode in knife.modes if isinstance(mode, MeleeMode)]
    assert len(knife_modes) == len(knife.modes)
    assert {mode.parry.modifier for mode in knife_modes if mode.parry} == {-1}
    staff = entries["equipment:quarterstaff"]
    staff_modes = [mode for mode in staff.modes if isinstance(mode, MeleeMode)]
    assert len(staff_modes) == len(staff.modes) == 4
    assert all(mode.hands == 2 for mode in staff_modes)
    assert [mode.parry.modifier for mode in staff_modes[:2] if mode.parry] == [2, 2]

    bastard = entries["equipment:bastard-sword"]
    bastard_modes = [mode for mode in bastard.modes if isinstance(mode, MeleeMode)]
    assert len(bastard_modes) == len(bastard.modes)
    assert [mode.hands for mode in bastard_modes] == [1, 1, 2, 2]
    assert [mode.parry.unbalanced for mode in bastard_modes if mode.parry] == [
        True,
        True,
        False,
        False,
    ]

    stake = entries["equipment:wooden-stake"].modes[0]
    assert isinstance(stake, MeleeMode)
    assert str(stake.damage.armor_divisor) == "0.5"
    prod = entries["equipment:cattle-prod"].modes[0]
    assert isinstance(prod, MeleeMode)
    assert (prod.damage.basis, prod.damage.dice, prod.damage.adds, prod.damage.damage_type) == (
        "fixed",
        1,
        -3,
        "burn",
    )
    glaive = entries["equipment:glaive"].modes[0]
    assert isinstance(glaive, MeleeMode) and glaive.ready_after_attack
    assert "conditional-ready-after-attack" in entries["equipment:glaive"].unsupported_mechanics


def test_b275_276_launcher_rows_have_independent_inventory_and_mode_facts() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    actual = []
    for (
        key,
        page,
        tl,
        cost,
        weight,
        minimum_st,
        accuracy,
        half_range,
        maximum_range,
        bulk,
    ) in RANGED_ROWS:
        entry = entries[key]
        assert (
            entry.provenance.pages,
            entry.technology_level,
            entry.price,
            entry.weight_millipounds,
        ) == (
            (page,),
            tl,
            cost,
            weight,
        )
        assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
        mode = entry.modes[0]
        assert (
            mode.minimum_st,
            mode.accuracy,
            mode.half_damage_range,
            mode.maximum_range,
            mode.bulk,
        ) == (minimum_st, accuracy, half_range, maximum_range, bulk)
        actual.append(key)
    assert actual == [row[0] for row in RANGED_ROWS]

    longbow = entries["longbow"].modes[0]
    assert isinstance(longbow, RangedMode) and longbow.rated_strength is not None
    assert longbow.rated_strength.model_dump() == {"kind": "bow", "st": 11}
    crossbow = entries["crossbow"].modes[0]
    assert isinstance(crossbow, RangedMode) and crossbow.reload_seconds == 4
    assert entries["pistol-crossbow"].unsupported_mechanics == ("one-handed-rated-crossbow",)


def test_b276_ammunition_keeps_fractional_prices_and_weights() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    expected = {
        "blowpipe-dart": ("0.1", 50),
        "arrow": ("2", 100),
        "bolt": ("2", 60),
        "lead-pellet": ("0.1", 60),
        "sling-stone": ("0", 50),
    }
    assert {
        key: (str(entries[key].price), entries[key].weight_millipounds) for key in expected
    } == expected
    assert all(entries[key].ammunition for key in expected)


def test_b278_firearms_preserve_independent_table_columns() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    for row in FIREARM_ROWS:
        (
            key,
            tl,
            cost,
            weight,
            dice,
            adds,
            damage_type,
            acc,
            half,
            maximum,
            rof,
            shots,
            reload,
            st,
            bulk,
            recoil,
        ) = row
        entry = entries[key]
        page = 279 if "shotgun" in key or key.startswith("blunderbuss") else 278
        assert (
            entry.provenance.pages,
            entry.technology_level,
            entry.price,
        ) == (
            (page,),
            tl,
            cost,
        )
        assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
        mode = entry.modes[0]
        assert (mode.damage.dice, mode.damage.adds, mode.damage.damage_type) == (
            dice,
            adds,
            damage_type,
        )
        assert (
            mode.accuracy,
            mode.half_damage_range,
            mode.maximum_range,
            mode.rate_of_fire,
            mode.shots,
            mode.reload_seconds,
            mode.minimum_st,
            mode.bulk,
            mode.recoil,
        ) == (acc, half, maximum, rof, shots, reload, st, bulk, recoil)
        assert mode.firearm and mode.firearm.technology_level == tl
        if "shotgun" in key or key.startswith("blunderbuss"):
            assert mode.hands == 2
            assert mode.multiple_projectiles is not None
            assert mode.multiple_projectiles.projectiles_per_shot == 9
        assert mode.ammunition_id is not None
        ammunition = entries[mode.ammunition_id.removeprefix("equipment:")]
        # B270 gives unloaded weight only for Shots 1. For Shots 2+, rebuild
        # the table's loaded figure from the physical weapon and its rounds.
        reconstructed = entry.weight_millipounds
        if shots > 1:
            reconstructed += ammunition.weight_millipounds * shots
        assert reconstructed == weight

    derringer = entries["derringer-41"].modes[0]
    revolver = entries["revolver-36"].modes[0]
    assert isinstance(derringer, RangedMode) and derringer.reload_protocol == "per-round"
    assert isinstance(revolver, RangedMode) and revolver.firearm is not None
    assert revolver.firearm.action == "revolver"
    for key, shots in (("auto-pistol-45-tl6", 8), ("auto-pistol-9mm-tl6", 9)):
        chambered_mode = entries[key].modes[0]
        assert isinstance(chambered_mode, RangedMode)
        assert (
            chambered_mode.shots,
            chambered_mode.chamber_capacity,
            chambered_mode.shots - chambered_mode.chamber_capacity,
        ) == (
            shots,
            1,
            shots - 1,
        )
    automatic = entries["smg-9mm-tl6"].modes[0]
    assert isinstance(automatic, RangedMode)
    assert (automatic.rate_of_fire, automatic.minimum_shots_per_attack) == (8, 2)


def test_b278_firearm_ammunition_uses_exact_per_round_units() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    expected = {
        "flintlock-pistol-51-round": ("0.2", 10),
        "wheel-lock-pistol-60-round": ("0.2", 10),
        "derringer-41-round": ("1", 50),
        "revolver-36-round": ("0.8", 40),
        "snub-revolver-38-round": ("0.8", 40),
        "auto-pistol-45-tl6-round": ("1.5", 75),
        "auto-pistol-9mm-tl6-round": ("8/9", Fraction(400, 9)),
        "smg-9mm-tl6-round": ("15/16", Fraction(375, 8)),
        "blunderbuss-8g-round": ("2.6", 130),
        "double-shotgun-10g-round": ("1", 50),
        "pump-shotgun-12g-round": ("2.8", 140),
        "auto-shotgun-12g-round": ("17/7", Fraction(850, 7)),
    }
    assert {
        key: (str(entries[key].price), entries[key].weight_millipounds) for key in expected
    } == expected
    assert all(entries[key].ammunition for key in expected)


def test_b279_single_shot_long_guns_preserve_independent_columns() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    for row in LONG_GUN_ROWS:
        (
            key,
            tl,
            cost,
            weight,
            dice,
            adds,
            damage_type,
            acc,
            half,
            maximum,
            reload,
            st,
            bulk,
            rcl,
        ) = row
        entry = entries[key]
        assert (entry.provenance.pages, entry.technology_level, entry.price) == ((279,), tl, cost)
        assert entry.weight_millipounds == weight
        assert entry.unsupported_mechanics == ("conditional-one-handed-firearm",)
        assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
        mode = entry.modes[0]
        assert (mode.damage.dice, mode.damage.adds, mode.damage.damage_type) == (
            dice,
            adds,
            damage_type,
        )
        assert (
            mode.accuracy,
            mode.half_damage_range,
            mode.maximum_range,
            mode.shots,
            mode.reload_seconds,
            mode.minimum_st,
            mode.hands,
            mode.bulk,
            mode.recoil,
        ) == (acc, half, maximum, 1, reload, st, 2, bulk, rcl)


def test_b279_single_shot_ammunition_uses_exact_load_units() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    expected = {
        "handgonne-90-round": ("2", 100),
        "flintlock-musket-75-round": ("1", 50),
        "rifle-musket-577-round": ("1", 50),
        "cartridge-rifle-45-round": ("2", 100),
    }
    assert {
        key: (str(entries[key].price), entries[key].weight_millipounds) for key in expected
    } == expected
    assert all(entries[key].ammunition for key in expected)


def test_b279_repeating_rifles_reconstruct_loaded_table_weight() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    for row in REPEATING_RIFLE_ROWS:
        (
            key,
            tl,
            cost,
            loaded_weight,
            dice,
            adds,
            accuracy,
            half,
            maximum,
            rate_of_fire,
            shots,
            chamber,
            reload_seconds,
            minimum_st,
            bulk,
            recoil,
        ) = row
        entry = entries[key]
        assert (entry.provenance.pages, entry.technology_level, entry.price) == ((279,), tl, cost)
        assert entry.unsupported_mechanics == ("conditional-one-handed-firearm",)
        assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
        mode = entry.modes[0]
        assert (mode.damage.dice, mode.damage.adds, mode.damage.damage_type) == (dice, adds, "pi")
        assert (
            mode.accuracy,
            mode.half_damage_range,
            mode.maximum_range,
            mode.rate_of_fire,
            mode.shots,
            mode.chamber_capacity,
            mode.reload_seconds,
            mode.minimum_st,
            mode.hands,
            mode.bulk,
            mode.recoil,
        ) == (
            accuracy,
            half,
            maximum,
            rate_of_fire,
            shots,
            chamber,
            reload_seconds,
            minimum_st,
            2,
            bulk,
            recoil,
        )
        assert mode.ammunition_id is not None
        ammunition = entries[mode.ammunition_id.removeprefix("equipment:")]
        assert entry.weight_millipounds + ammunition.weight_millipounds * shots == loaded_weight

    lever = entries["lever-action-carbine-30"].modes[0]
    assert isinstance(lever, RangedMode) and lever.reload_protocol == "per-round"


def test_b279_repeating_rifle_ammunition_exact_values() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    expected = {
        "lever-action-carbine-30-round": ("1", "50"),
        "bolt-action-rifle-762-round": ("6/5", "60"),
        "self-loading-rifle-762-round": ("5/4", "125/2"),
        "assault-rifle-556-round": ("2/3", "100/3"),
        "assault-rifle-762s-round": ("6/5", "60"),
        "battle-rifle-762-round": ("17/10", "85"),
        "assault-carbine-556-round": ("2/3", "100/3"),
    }
    assert {
        key: (str(entries[key].price), str(entries[key].weight_millipounds)) for key in expected
    } == expected


def test_remaining_b278_handguns_reconstruct_loaded_table_weight() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    for row in ORDINARY_HANDGUN_ROWS:
        (
            key,
            skill,
            tl,
            cost,
            loaded_weight,
            dice,
            adds,
            damage_type,
            accuracy,
            half,
            maximum,
            rate_of_fire,
            shots,
            chamber,
            reload_seconds,
            minimum_st,
            bulk,
            recoil,
        ) = row
        entry = entries[key]
        assert (entry.provenance.pages, entry.technology_level, entry.price) == ((278,), tl, cost)
        assert entry.unsupported_mechanics == ()
        assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
        mode = entry.modes[0]
        assert mode.skill_id == "skill:guns-" + skill
        assert (mode.damage.dice, mode.damage.adds, mode.damage.damage_type) == (
            dice,
            adds,
            damage_type,
        )
        assert (
            mode.accuracy,
            mode.half_damage_range,
            mode.maximum_range,
            mode.rate_of_fire,
            mode.shots,
            mode.chamber_capacity,
            mode.reload_seconds,
            mode.minimum_st,
            mode.hands,
            mode.bulk,
            mode.recoil,
        ) == (
            accuracy,
            half,
            maximum,
            rate_of_fire,
            shots,
            chamber,
            reload_seconds,
            minimum_st,
            1,
            bulk,
            recoil,
        )
        assert mode.ammunition_id is not None
        ammunition = entries[mode.ammunition_id.removeprefix("equipment:")]
        assert entry.weight_millipounds + ammunition.weight_millipounds * shots == loaded_weight


def test_remaining_b278_handgun_ammunition_exact_values() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    expected = {
        "revolver-38-round": ("2/3", "100/3"),
        "auto-pistol-9mm-tl7-round": ("0.8", "40"),
        "holdout-pistol-380-round": ("0.8", "40"),
        "revolver-357m-round": ("0.7", "35"),
        "revolver-44m-round": ("1", "50"),
        "auto-pistol-44m-round": ("4/3", "200/3"),
        "auto-pistol-40-round": ("14/15", "140/3"),
        "machine-pistol-9mm-round": ("22/25", "44"),
    }
    assert {
        key: (str(entries[key].price), str(entries[key].weight_millipounds)) for key in expected
    } == expected


def test_b287_shields_preserve_independent_table_columns() -> None:
    entries = {
        entry.definition_id.removeprefix("equipment:"): entry for entry in BASIC_EQUIPMENT.entries
    }
    for key, tl, db, cost, weight, dr, hp in SHIELD_ROWS:
        entry = entries[key]
        assert (
            entry.provenance.pages,
            entry.technology_level,
            entry.price,
            entry.weight_millipounds,
            entry.slot,
        ) == ((287,), tl, cost, weight, "shield")
        assert entry.shield is not None
        assert (entry.shield.skill_id, entry.shield.defense_bonus, entry.shield.can_block) == (
            "skill:shield",
            db,
            True,
        )
        assert entry.durability is not None
        assert (entry.durability.dr, entry.durability.hp) == (dr, hp)

    assert set(entries) >= {row[0] for row in SHIELD_ROWS}


def test_sword_and_armor_independent_combat_facts() -> None:
    sword = BASIC_EQUIPMENT.entries[1]
    assert sword.durability and (sword.durability.hp, sword.durability.dr, sword.durability.ht) == (
        12,
        6,
        12,
    )
    assert [(m.id, m.damage.basis, m.damage.adds, m.damage.damage_type) for m in sword.modes] == [
        ("swing", "swing", 1, "cut"),
        ("thrust", "thrust", 1, "cr"),
    ]
    assert all(
        isinstance(m, MeleeMode) and m.reach == (1,) and m.minimum_st == 10 for m in sword.modes
    )
    armor = next(e for e in BASIC_EQUIPMENT.entries if e.definition_id == "equipment:leather-armor")
    assert armor.armor and armor.armor.dr == 2 and not armor.armor.flexible
    assert armor.armor.locations == ("torso", "groin")
    assert [(e.armor.dr, e.armor.locations) for e in BASIC_EQUIPMENT.entries if e.armor] == [
        (5, ("torso", "groin")),
        (2, ("torso", "groin")),
        (3, ("torso",)),
        (5, ("torso",)),
        (4, ("torso", "groin")),
        (7, ("torso", "groin")),
        (6, ("torso", "groin")),
        (5, ("torso", "groin")),
    ]


def test_container_units_and_unsupported_activation() -> None:
    pack = next(e for e in BASIC_EQUIPMENT.entries if e.definition_id == "equipment:frame-backpack")
    assert pack.inventory_spec().container_capacity == 100000
    for entry in ULTRATECH_INDEX + tuple(
        e for e in BASIC_EQUIPMENT.entries if e.unsupported_mechanics
    ):
        with pytest.raises(ValidationError, match="unsupported"):
            entry.inventory_spec()
    assert [(v.hp, v.dr, v.price) for v in VEHICLE_INDEX] == [(35, 2, 680), (57, 5, 30000)]
    for vehicle in VEHICLE_INDEX:
        with pytest.raises(ValidationError, match="#358"):
            vehicle.require_operation()


def test_catalog_roundtrip_and_profile_gate() -> None:
    assert (
        EquipmentCatalog.model_validate_json(BASIC_EQUIPMENT.model_dump_json()) == BASIC_EQUIPMENT
    )
    with pytest.raises(SchemaError, match="exact Basic Set"):
        EquipmentCatalog(profile_id="gurps-lite-4e-2004", entries=BASIC_EQUIPMENT.entries)
