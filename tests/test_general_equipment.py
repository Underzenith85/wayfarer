"""Independent B264-B289 and Campaigns B345/B411/B425 equipment fixtures."""

from decimal import Decimal

import pytest

from wayfarer.certification.equipment_audit import ledger
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.types.equipment import UltraTechDrugSpec
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Armor, layered_armor
from wayfarer.engine.simulation.equipment.general import (
    AttachEquipment,
    EquipmentContext,
    UseEquipment,
    accessory_effects,
    apply_equipment,
    equipment_modifier,
    starting_wealth,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

SCOPED_SECTIONS = {
    "general-equipment-b288": 36,
    "general-equipment-remainder": 46,
    "weapon-accessories": 12,
}


def engine() -> ResourceEngine:
    world = World(entities=(Entity("a", EntityKind.ACTOR, "Operator"),))
    return ResourceEngine(
        world,
        GURPS_BASIC_PROFILE.catalog,
        GURPS_BASIC_PROFILE.rules,
        GURPS_BASIC_PROFILE.policy,
        BASIC_EQUIPMENT.bind(GURPS_BASIC_PROFILE.packages),
    )


def state(*items: Item) -> ResourceState:
    return ResourceState(items=items, owners=(Owner(actor_id="a", capacity=1_000_000),))


def test_catalog_completes_all_94_scoped_rows() -> None:
    entries = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}
    current = ledger()
    completed: set[str] = set()
    for section_id, formerly_blocked in SCOPED_SECTIONS.items():
        section = next(row for row in current.sections if row.id == section_id)
        rows = [entries["equipment:" + identifier] for identifier in section.selected]
        scoped = [row for row in rows if row.uses or row.fuel or row.accessory]
        assert len(scoped) == formerly_blocked
        assert all(not row.unsupported_mechanics for row in rows)
        completed.update(row.definition_id for row in scoped)
    assert len(completed) == 94

    assert entries["equipment:steel-cable-15"].uses[0].capacity == 3_700
    assert entries["equipment:insulated-sleeping-bag"].uses[0].modifier == 3
    assert entries["equipment:scuba-gear"].uses[0].duration_seconds == 7_200
    assert entries["equipment:war-saddle"].uses[1].protection == 50
    assert entries["equipment:audio-bug"].uses[0].range_yards == 440
    assert entries["equipment:shotgun-microphone"].uses[0].modifier == 1
    assert entries["equipment:wheelbarrow"].uses[0].capacity == 350


def test_fuel_consumption_is_conserved_and_replay_safe() -> None:
    reducer = engine()
    before = state(
        Item(id="stove", definition_id="equipment:camp-stove", owner_id="a"),
        Item(
            id="fuel",
            definition_id="equipment:kerosene-gallon",
            owner_id="a",
            charges=4,
        ),
    )
    command = UseEquipment(
        id="cook",
        actor_id="a",
        expected_revision=0,
        item_id="stove",
        use_id="operate",
        duration_seconds=4 * 60 * 60,
        consumable_item_id="fuel",
    )
    context = EquipmentContext(campaign_technology_level=6)
    after, outcome = apply_equipment(reducer, before, command, context, system=True)
    assert (outcome.consumed_item_id, outcome.consumed_quantity) == ("fuel", 1)
    assert outcome.duration_seconds == 4 * 60 * 60
    assert next(item for item in after.items if item.id == "fuel").charges == 3
    restored = ResourceState.model_validate_json(after.model_dump_json())
    replay, same = apply_equipment(reducer, restored, command, context, system=True)
    assert replay == restored and same == outcome
    with pytest.raises(ConflictError, match="different payload"):
        apply_equipment(
            reducer,
            after,
            command.model_copy(update={"duration_seconds": 1}),
            context,
            system=True,
        )


def test_accessory_attachment_and_firearm_effects_are_exact() -> None:
    reducer = engine()
    before = state(
        Item(id="pistol", definition_id="equipment:auto-pistol-9mm-tl7", owner_id="a"),
        Item(id="laser", definition_id="equipment:laser-sight", owner_id="a"),
        Item(id="scope", definition_id="equipment:scope-4x", owner_id="a"),
        Item(id="silencer", definition_id="equipment:pistol-smg-silencer", owner_id="a"),
    )
    context = EquipmentContext(campaign_technology_level=8)
    current = before
    for revision, accessory in enumerate(("laser", "scope", "silencer")):
        current, outcome = apply_equipment(
            reducer,
            current,
            AttachEquipment(
                id="attach-" + accessory,
                actor_id="a",
                expected_revision=revision,
                accessory_item_id=accessory,
                target_item_id="pistol",
            ),
            context,
            system=True,
        )
        assert outcome.status == "attached"
    assert accessory_effects(
        reducer,
        current,
        "pistol",
        aimed_seconds=2,
        aiming_dot_visible_to_target=True,
    ) == {
        "attack_modifier": 1,
        "target_dodge_modifier": 1,
        "accuracy_modifier": 2,
        "hearing_modifier": -4,
        "damage_modifier_per_die": -1,
        "infravision": False,
    }

    with pytest.raises(ValidationError, match="incompatible"):
        apply_equipment(
            reducer,
            before,
            AttachEquipment(
                id="bad-scope",
                actor_id="a",
                expected_revision=0,
                accessory_item_id="scope",
                target_item_id=None,
            ),
            context,
            system=True,
        )


def test_tl_legality_and_consumable_failures_are_explicit() -> None:
    reducer = engine()
    medical = state(Item(id="kit", definition_id="equipment:first-aid-kit", owner_id="a"))
    command = UseEquipment(
        id="aid", actor_id="a", expected_revision=0, item_id="kit", use_id="first-aid"
    )
    with pytest.raises(ValidationError, match="technology level"):
        apply_equipment(
            reducer,
            medical,
            command,
            EquipmentContext(campaign_technology_level=5),
            system=True,
        )
    after, outcome = apply_equipment(
        reducer,
        medical,
        command,
        EquipmentContext(campaign_technology_level=6, skill_technology_level=6),
        system=True,
    )
    assert after.revision == 1 and outcome.modifier == 1

    camera = state(Item(id="camera", definition_id="equipment:camera-35mm", owner_id="a"))
    with pytest.raises(ValidationError, match="consumable"):
        apply_equipment(
            reducer,
            camera,
            UseEquipment(
                id="photo",
                actor_id="a",
                expected_revision=0,
                item_id="camera",
                use_id="photograph",
            ),
            EquipmentContext(campaign_technology_level=6),
            system=True,
        )

    illegal = reducer.specs["equipment:lockpicks"].model_copy(update={"legality_class": 4})
    reducer.specs[illegal.definition_id] = illegal
    with pytest.raises(ValidationError, match="legality"):
        apply_equipment(
            reducer,
            state(Item(id="picks", definition_id="equipment:lockpicks", owner_id="a")),
            UseEquipment(
                id="pick",
                actor_id="a",
                expected_revision=0,
                item_id="picks",
                use_id="pick-lock",
            ),
            EquipmentContext(campaign_technology_level=3, maximum_legality_class=3),
            system=True,
        )


def test_split_front_only_and_layered_armor() -> None:
    inner = Armor(
        locations=("torso",),
        dr=4,
        alternate_dr=2,
        alternate_damage_types=("cr",),
        flexible=True,
        concealable=True,
    )
    outer = Armor(locations=("torso",), dr=5, front_only=True)
    assert inner.protection("torso", "cut", facing="rear") == 4
    assert inner.protection("torso", "cr", facing="rear") == 2
    assert outer.protection("torso", "cut", facing="rear") == 0
    assert layered_armor((inner, outer), "torso", "cut", facing="front") == (9, -1)
    with pytest.raises(ValidationError, match="flexible and concealable"):
        layered_armor((outer, inner), "torso", "cut", facing="front")


def test_equipment_modifiers_starting_wealth_and_drug_design() -> None:
    assert equipment_modifier("none", technological=True, technology_level=8) == -10
    assert equipment_modifier("improvised", technological=False, technology_level=3) == -2
    assert equipment_modifier("best", technological=True, technology_level=9) == 4
    assert (
        equipment_modifier(
            "fine",
            technological=True,
            technology_level=8,
            missing_important_items=1,
            damage_penalty=2,
        )
        == -1
    )
    assert starting_wealth(8) == Decimal(20_000)
    assert starting_wealth(3, Decimal("0.2")) == Decimal(200)

    truth_drug = UltraTechDrugSpec(
        id="truth-drug",
        trait_point_values=(-20,),
        duration="short",
        potency=3,
        form="injection",
        legality_class=2,
    )
    assert truth_drug.duration_seconds(10) == 15 * 60
    assert truth_drug.cost() == Decimal(320)
