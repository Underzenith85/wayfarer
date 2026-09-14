"""Acceptance evidence for #686 (B288-289, B345, B411 and B425)."""

from types import SimpleNamespace
from typing import cast

import pytest

from wayfarer.engine.rules.traits.background import BackgroundTraits
from wayfarer.engine.rules.types.general_equipment import EquipmentQuality
from wayfarer.engine.rules.ultratech_drugs import UltraTechDrug
from wayfarer.engine.simulation.campaign.law import (
    Jurisdiction,
    LawRules,
    LawState,
    LegalityRule,
    availability,
)
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Armor
from wayfarer.engine.simulation.equipment.general import (
    AttachAccessory,
    RetrieveLanyard,
    UseGeneralEquipment,
    apply_general_equipment,
)
from wayfarer.engine.simulation.health.hit_locations import (
    armor_concealment_penalty,
    armor_layering_penalty,
    armor_resistance,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def test_all_94_general_equipment_blockers_are_executable() -> None:
    rows = tuple(value for value in BASIC_EQUIPMENT.entries if value.general)
    assert len(rows) == 94
    assert not any(value.unsupported_mechanics for value in rows)
    facts = {value.definition_id: value.general for value in rows}
    assert facts["equipment:insulated-sleeping-bag"][0].modifier == 3
    assert facts["equipment:laser-sight"][0].duration_seconds == 6 * 3600
    assert facts["equipment:shoulder-holster"][0].modifier == -1
    assert facts["equipment:shoulder-holster"][0].grants_holdout
    assert facts["equipment:pistol-smg-silencer"][0].target == "weapon"
    assert facts["equipment:portable-carpentry-tool-kit"][0].skill_id == "skill:carpentry"


def test_general_use_consumes_exact_fuel_and_replays() -> None:
    stove = next(
        value for value in BASIC_EQUIPMENT.entries if value.definition_id == "equipment:camp-stove"
    )
    specs = {
        stove.definition_id: stove.inventory_spec(),
        "equipment:kerosene-gallon": EquipmentSpec(
            definition_id="equipment:kerosene-gallon", unit_weight=6400
        ),
    }
    engine = cast(ResourceEngine, SimpleNamespace(specs=specs, validate=lambda state: None))
    state = ResourceState(
        items=(
            Item(id="stove", definition_id=stove.definition_id, owner_id="a"),
            Item(
                id="fuel",
                definition_id="equipment:kerosene-gallon",
                owner_id="a",
                charges=16 * 3600,
            ),
        )
    )
    command = UseGeneralEquipment(
        id="cook",
        actor_id="a",
        expected_revision=0,
        item_id="stove",
        consumable_item_id="fuel",
        duration_seconds=4 * 3600,
    )
    used, result = apply_general_equipment(engine, state, command, actor_technology_level=8)
    assert result.active_seconds == 4 * 3600
    assert next(value for value in used.items if value.id == "fuel").charges == 12 * 3600
    assert apply_general_equipment(engine, used, command, actor_technology_level=8) == (
        used,
        result,
    )
    with pytest.raises(ConflictError):
        apply_general_equipment(
            engine,
            used,
            command.model_copy(update={"duration_seconds": 1}),
            actor_technology_level=8,
        )


def test_cold_resistance_bonus_is_a_persisted_use_effect() -> None:
    bag = next(
        value
        for value in BASIC_EQUIPMENT.entries
        if value.definition_id == "equipment:insulated-sleeping-bag"
    )
    engine = cast(
        ResourceEngine,
        SimpleNamespace(
            specs={bag.definition_id: bag.inventory_spec()}, validate=lambda state: None
        ),
    )
    state = ResourceState(items=(Item(id="bag", definition_id=bag.definition_id, owner_id="a"),))
    command = UseGeneralEquipment(id="sleep-warm", actor_id="a", expected_revision=0, item_id="bag")
    used, result = apply_general_equipment(engine, state, command, actor_technology_level=7)
    assert (result.modifier, result.feature.trait_id, result.feature.target) == (
        3,
        "hazard:freezing",
        "actor",
    )
    assert apply_general_equipment(engine, used, command, actor_technology_level=7) == (
        used,
        result,
    )


def test_special_tool_effect_and_technology_gate_are_material() -> None:
    entries = {value.definition_id: value for value in BASIC_EQUIPMENT.entries}
    compass = entries["equipment:compass"]
    kit = entries["equipment:portable-carpentry-tool-kit"]
    engine = cast(
        ResourceEngine,
        SimpleNamespace(
            specs={
                compass.definition_id: compass.inventory_spec(),
                kit.definition_id: kit.inventory_spec(),
            },
            validate=lambda state: None,
        ),
    )
    state = ResourceState(
        items=(
            Item(id="compass", definition_id=compass.definition_id, owner_id="a"),
            Item(id="kit", definition_id=kit.definition_id, owner_id="a"),
        )
    )
    used, result = apply_general_equipment(
        engine,
        state,
        UseGeneralEquipment(id="navigate", actor_id="a", expected_revision=0, item_id="compass"),
        actor_technology_level=6,
    )
    assert (result.feature.skill_id, result.modifier) == ("skill:navigation", 1)
    command = UseGeneralEquipment(id="carpentry", actor_id="a", expected_revision=1, item_id="kit")
    with pytest.raises(ValidationError, match="technology level"):
        apply_general_equipment(engine, used, command, actor_technology_level=0)
    _, result = apply_general_equipment(engine, used, command, actor_technology_level=1)
    assert result.feature.skill_id == "skill:carpentry"


def test_weapon_accessories_change_only_the_bound_target() -> None:
    lanyard = next(
        value
        for value in BASIC_EQUIPMENT.entries
        if value.definition_id == "equipment:leather-lanyard"
    )
    laser = next(
        value for value in BASIC_EQUIPMENT.entries if value.definition_id == "equipment:laser-sight"
    )
    specs = {
        lanyard.definition_id: lanyard.inventory_spec(),
        laser.definition_id: laser.inventory_spec(),
        "weapon:pistol": EquipmentSpec(
            definition_id="weapon:pistol", unit_weight=2, stackable=False, slot="hand"
        ),
    }
    engine = cast(ResourceEngine, SimpleNamespace(specs=specs, validate=lambda state: None))
    state = ResourceState(
        items=(
            Item(id="line", definition_id=lanyard.definition_id, owner_id="a"),
            Item(
                id="laser",
                definition_id=laser.definition_id,
                owner_id="a",
                charges=6 * 3600,
            ),
            Item(id="pistol", definition_id="weapon:pistol", owner_id="a"),
        )
    )
    attached, result = apply_general_equipment(
        engine,
        state,
        AttachAccessory(
            id="attach", actor_id="a", expected_revision=0, item_id="line", target_item_id="pistol"
        ),
        actor_technology_level=8,
    )
    assert (result.status, result.target_item_id) == ("attached", "pistol")
    retrieved, result = apply_general_equipment(
        engine,
        attached,
        RetrieveLanyard(
            id="retrieve",
            actor_id="a",
            expected_revision=1,
            item_id="line",
            target_item_id="pistol",
        ),
        actor_technology_level=8,
    )
    assert result.status == "retrieved"
    aimed, result = apply_general_equipment(
        engine,
        retrieved,
        AttachAccessory(
            id="sight",
            actor_id="a",
            expected_revision=2,
            item_id="laser",
            target_item_id="pistol",
        ),
        actor_technology_level=8,
    )
    assert (result.target_item_id, result.modifier, result.feature.duration_seconds) == (
        "pistol",
        1,
        6 * 3600,
    )
    assert ResourceState.model_validate_json(aimed.model_dump_json()) == aimed


def test_equipment_quality_and_ultratech_drug_source_math() -> None:
    assert EquipmentQuality(grade="none", technological_task=True).modifier == -10
    assert EquipmentQuality(grade="improvised", technological_task=False).modifier == -2
    assert (
        EquipmentQuality(
            grade="best", technology_level=9, missing_important_items=2, damage_penalty=3
        ).modifier
        == -1
    )
    assert EquipmentQuality(grade="fine").cost_multiplier == 20
    drug = UltraTechDrug(
        id="stim", absolute_point_value=10, duration="long", potency_penalty=-2, delivery="contact"
    )
    assert (drug.cost, drug.duration_seconds(ht=10), drug.resistance_modifier) == (4000, 86400, -2)
    assert UltraTechDrug.dose_resistance_modifier(4) == -2
    with pytest.raises(ValueError):
        UltraTechDrug(id="bad", absolute_point_value=1, duration="very-long")


def test_split_dr_selects_the_authored_damage_type() -> None:
    mail = Armor(
        locations=("torso",),
        dr=4,
        split_dr=(("cr", 2),),
    )
    assert armor_resistance((mail,), "torso", damage_type="cut") == 4
    assert armor_resistance((mail,), "torso", damage_type="cr") == 2


def test_front_only_dr_rejects_a_rear_attack() -> None:
    breastplate = Armor(locations=("torso",), dr=5, front_only=True)
    assert armor_resistance((breastplate,), "torso", attack_from_front=True) == 5
    assert armor_resistance((breastplate,), "torso", attack_from_front=False) == 0


def test_layered_armor_combines_dr_and_applies_the_dx_cost() -> None:
    mail = Armor(locations=("torso",), dr=4, flexible=True)
    plate = Armor(locations=("torso",), dr=5, layer=1)
    assert armor_resistance((mail, plate), "torso") == 9
    assert armor_layering_penalty((mail, plate), "torso") == -1
    assert armor_layering_penalty((mail, plate), "skull") == 0


def test_concealed_armor_accumulates_the_reaction_penalty() -> None:
    mail = Armor(locations=("torso",), dr=4, concealment_penalty=-1)
    plate = Armor(locations=("torso",), dr=5, concealment_penalty=-2)
    assert armor_concealment_penalty((mail, plate)) == -3


def test_starting_wealth_bounds_equipment_budget() -> None:
    assert BackgroundTraits(wealth="comfortable").starting_assets(1_000) == 2_000
    stove = next(
        value for value in BASIC_EQUIPMENT.entries if value.definition_id == "equipment:camp-stove"
    )
    assert BackgroundTraits(wealth="poor").starting_assets(1_000) == stove.price * 4


def test_legality_class_uses_the_catalog_rating_and_jurisdiction() -> None:
    shield = next(
        value
        for value in BASIC_EQUIPMENT.entries
        if value.definition_id == "equipment:force-shield"
    )
    assert shield.legality_class == 3
    laws = LawRules(
        id="law",
        version=1,
        jurisdictions=(Jurisdiction(id="city", control_rating=3),),
        legality=(
            LegalityRule(definition_id=shield.definition_id, legality_class=shield.legality_class),
        ),
    )
    result = availability(
        laws,
        LawState(),
        jurisdiction_id="city",
        definition_id=shield.definition_id,
        actor_id="a",
    )
    assert result.availability == "registered"
