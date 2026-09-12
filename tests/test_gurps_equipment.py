"""Independent numeric expectations: Lite August 2004 rev. 07/12/04, pp. 19-20.

Sample source audit remains outstanding; these tests are not certification.
"""

from dataclasses import replace
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as SchemaError

from wayfarer.engine.character.statistics import PrimaryAttributes, compile_statistics
from wayfarer.engine.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    RulesPackage,
    SourceReference,
)
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_EQUIPMENT,
    Armor,
    Damage,
    EquipmentCatalog,
    MeleeMode,
    RangedMode,
    inventory_load,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Equip, Item, Owner, ResourceState, Transfer
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError


def package() -> RulesPackage:
    return RulesPackage(
        id="equipment:test",
        version="1",
        edition="gurps-lite-4e-2004",
        sources=(
            SourceReference("sjg:gurps-lite-4e-2004", "GURPS Lite", "user-supplied-reference"),
        ),
        definitions=tuple(
            RuleDefinition(
                key, kind, key, "sjg:gurps-lite-4e-2004", None, ImplementationStatus.IMPLEMENTED
            )
            for key, kind in (
                ("equipment:broadsword", DefinitionKind.EQUIPMENT),
                ("equipment:leather-armor", DefinitionKind.EQUIPMENT),
                ("skill:broadsword", DefinitionKind.SKILL),
            )
        ),
    )


def engine() -> ResourceEngine:
    p = package()
    policy = replace(
        DEFAULT_POLICY,
        permitted_sources=frozenset({p.sources[0].id}),
        allowed_equipment=frozenset(e.definition_id for e in LITE_EQUIPMENT.entries),
    )
    rules = replace(
        DEFAULT_RULES, edition=p.edition, packages=(PackagePin(p.id, p.version, p.digest),)
    )
    return ResourceEngine(
        World(entities=(Entity("a", EntityKind.ACTOR, "A"), Entity("b", EntityKind.ACTOR, "B"))),
        RulesCatalog((p,)),
        rules,
        policy,
        LITE_EQUIPMENT.bind((p,)),
    )


def test_numeric_sample_roundtrip() -> None:
    restored = EquipmentCatalog.model_validate_json(LITE_EQUIPMENT.model_dump_json())
    assert restored == LITE_EQUIPMENT
    sword, armor = restored.entries
    assert (sword.weight_millipounds, sword.price, sword.technology_level) == (3000, 500, 2)
    assert [
        (m.damage.basis, m.damage.adds, m.damage.damage_type, m.minimum_st) for m in sword.modes
    ] == [("swing", 1, "cut", 10), ("thrust", 1, "cr", 10)]
    assert (armor.weight_millipounds, armor.price) == (10000, 100)
    assert armor.armor is not None and armor.armor.dr == 2
    assert EquipmentCatalog.model_json_schema()["$defs"]["RangedMode"]


@pytest.mark.parametrize("basis,dice", [("fixed", None), ("thrust", 1), ("swing", 2)])
def test_impossible_damage(basis: str, dice: int | None) -> None:
    with pytest.raises(SchemaError):
        Damage.model_validate({"basis": basis, "dice": dice, "damage_type": "cut"})


def test_modes_and_references_fail_closed() -> None:
    with pytest.raises(SchemaError):
        MeleeMode(
            id="bad",
            skill_id="skill:x",
            minimum_st=0,
            damage=Damage(basis="swing", damage_type="cut"),
            reach=(1,),
        )
    with pytest.raises(SchemaError):
        Armor(locations=("torso", "torso"), dr=1)
    with pytest.raises(ValidationError, match="skill"):
        LITE_EQUIPMENT.bind((replace(package(), definitions=package().definitions[:2]),))
    with pytest.raises(ValidationError, match="source"):
        LITE_EQUIPMENT.bind((replace(package(), sources=()),))
    with pytest.raises(SchemaError):
        EquipmentCatalog(profile_id="gurps-lite-4e-2004", entries=LITE_EQUIPMENT.entries * 2)


def test_ranged_roundtrip_and_missing_ammunition() -> None:
    mode = RangedMode(
        id="shot",
        skill_id="skill:bow",
        minimum_st=10,
        hands=2,
        damage=Damage(basis="thrust", damage_type="imp"),
        accuracy=1,
        range_basis="st",
        half_damage_range=10,
        maximum_range=15,
        shots=1,
        reload_seconds=2,
        bulk=-6,
        ammunition_id="equipment:arrow",
    )
    assert RangedMode.model_validate_json(mode.model_dump_json()) == mode
    with pytest.raises(SchemaError, match="range"):
        RangedMode.model_validate({**mode.model_dump(), "maximum_range": 5})
    entry = LITE_EQUIPMENT.entries[0]
    with pytest.raises(SchemaError, match="ammunition"):
        EquipmentCatalog(
            profile_id="gurps-basic-set-4e-2004",
            entries=(entry.model_copy(update={"modes": (mode,)}),),
        )
    # B182 Bow is pinned in the Basic Set package only; a Lite catalog cannot claim it.
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        EquipmentCatalog(
            profile_id="gurps-lite-4e-2004",
            entries=(entry.model_copy(update={"modes": (mode,)}),),
        )


@given(st.integers(min_value=1, max_value=10))
def test_transfer_equip_load_conservation(quantity: int) -> None:
    service = engine()
    state = ResourceState(
        items=tuple(
            Item(id=f"armor-{i}", definition_id="equipment:leather-armor", owner_id="a")
            for i in range(quantity)
        ),
        owners=(Owner(actor_id="a", capacity=200000), Owner(actor_id="b", capacity=200000)),
    )
    stats = compile_statistics("gurps-lite-4e-2004", PrimaryAttributes(10, 10, 10, 10))
    before = inventory_load(LITE_EQUIPMENT, service, state, "a", stats)
    assert before.weight_pounds == Decimal(10 * quantity)
    command = Transfer(
        id="give", actor_id="a", expected_revision=0, item_id="armor-0", quantity=1, owner_id="b"
    )
    transferred = service.apply(state, command)
    assert service.apply(transferred, command) == transferred
    equipped = service.apply(
        transferred, Equip(id="wear", actor_id="b", expected_revision=1, item_id="armor-0")
    )
    a = inventory_load(LITE_EQUIPMENT, service, equipped, "a", stats)
    b = inventory_load(LITE_EQUIPMENT, service, equipped, "b", stats)
    assert a.weight_pounds + b.weight_pounds == before.weight_pounds
    assert (b.weight_pounds, b.level, b.move, b.dodge) == (Decimal(10), 0, 5, 8)
    assert len(equipped.items) == quantity
    assert {i.id for i in equipped.items} == {i.id for i in state.items}
    with pytest.raises(ConflictError):
        service.apply(
            equipped, Equip(id="stale", actor_id="b", expected_revision=0, item_id="armor-0")
        )
    with pytest.raises(ValidationError, match="owner"):
        inventory_load(LITE_EQUIPMENT, service, equipped, "unknown", stats)


def test_load_bands_overload_and_unit_guard() -> None:
    service = engine()
    stats = compile_statistics("gurps-lite-4e-2004", PrimaryAttributes(10, 10, 10, 10))
    # Lite p. 22: BL 20 lb, thresholds 20/40/60/120/200 lb.
    for quantity, level, move, dodge in (
        (2, 0, 5, 8),
        (3, 1, 4, 7),
        (5, 2, 3, 6),
        (7, 3, 2, 5),
        (13, 4, 1, 4),
        (21, None, None, None),
    ):
        state = ResourceState(
            items=tuple(
                Item(id=f"a{i}", definition_id="equipment:leather-armor", owner_id="a")
                for i in range(quantity)
            ),
            owners=(Owner(actor_id="a", capacity=300000),),
        )
        load = inventory_load(LITE_EQUIPMENT, service, state, "a", stats)
        assert (load.level, load.move, load.dodge) == (level, move, dodge)
    basic = compile_statistics("gurps-basic-set-4e-2004", PrimaryAttributes(10, 10, 10, 10))
    with pytest.raises(ValidationError, match="profile"):
        inventory_load(LITE_EQUIPMENT, service, state, "a", basic)
    service.specs["equipment:leather-armor"] = service.specs["equipment:leather-armor"].model_copy(
        update={"unit_weight": 10}
    )
    with pytest.raises(ValidationError, match="units"):
        inventory_load(LITE_EQUIPMENT, service, state, "a", stats)


def test_fractional_range_and_illegal_thrown_mode() -> None:
    mode = RangedMode(
        id="throw",
        skill_id="skill:thrown-axe",
        minimum_st=11,
        damage=Damage(basis="swing", adds=2, damage_type="cut"),
        accuracy=2,
        range_basis="st",
        half_damage_range=1,
        maximum_range=Decimal("1.5"),
        shots=1,
        reload_seconds=0,
        bulk=-3,
        thrown=True,
    )
    assert RangedMode.model_validate_json(mode.model_dump_json()) == mode
    with pytest.raises(SchemaError, match="Thrown"):
        RangedMode.model_validate({**mode.model_dump(), "shots": 2})
