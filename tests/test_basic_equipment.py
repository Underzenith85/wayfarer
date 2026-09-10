"""Independent selected-row audit: Characters third printing B271,280,283,288."""

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.simulation.basic_equipment import BASIC_EQUIPMENT, ULTRATECH_INDEX, VEHICLE_INDEX
from wayfarer.simulation.gurps_equipment import EquipmentCatalog, MeleeMode


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
