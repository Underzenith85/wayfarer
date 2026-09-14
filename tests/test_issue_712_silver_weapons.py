"""Independent B275 silver weapon construction and injury evidence (#712)."""

from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.certification.equipment_audit import PINNED_PACKAGES
from wayfarer.engine.character.traits.attack_defense import (
    AttackDefenseTraits,
    PurchasedAttackDefenseTrait,
)
from wayfarer.engine.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import ObjectCondition
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, RangedMode
from wayfarer.engine.simulation.equipment.silver import (
    attack_construction,
    breakage_quality,
    silver_wounding_multiplier,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    AmmunitionLoad,
    Item,
    Owner,
    Pool,
    ResourceState,
    SilverConstruction,
    Transfer,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


def entries() -> dict[str, EquipmentProfile]:
    return {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}


def resource_engine(*, technology_level: int | None = None) -> ResourceEngine:
    catalog = RulesCatalog(PINNED_PACKAGES)
    policy = CampaignPolicy(
        id="silver-test",
        version=1,
        point_budget=100,
        disadvantage_limit=100,
        attribute_ceiling=20,
        skill_ceiling=30,
        permitted_sources=frozenset(
            source.id for package in PINNED_PACKAGES for source in package.sources
        ),
        allowed_equipment=frozenset(entry.definition_id for entry in BASIC_EQUIPMENT.entries),
        allow_supernatural=True,
        technology_level=technology_level,
    )
    rules = CampaignRules(
        edition=PINNED_PACKAGES[0].edition,
        packages=tuple(PackagePin(p.id, p.version, p.digest) for p in PINNED_PACKAGES),
        policy_id=policy.id,
        policy_version=policy.version,
    )
    specs = BASIC_EQUIPMENT.bind(PINNED_PACKAGES)
    if technology_level is not None:
        specs = tuple(spec for spec in specs if spec.technology_level <= technology_level)
    return ResourceEngine(
        World(entities=(Entity("a", EntityKind.ACTOR, "A"), Entity("b", EntityKind.ACTOR, "B"))),
        catalog,
        rules,
        policy,
        specs,
    )


def test_source_exact_choices_cost_constraints_and_mode_identity() -> None:
    current = entries()
    sword = current["equipment:broadsword"]
    assert sword.silver_construction == "melee-weapon"
    assert sword.price_for("solid-silver") == 10_000
    assert sword.price_for("silver-coated") == 1_500
    assert sword.price_for() == 500
    assert sword.modes == current["equipment:broadsword"].modes
    assert breakage_quality("solid-silver", "resistant") == "cheap"
    assert breakage_quality("silver-coated", "resistant") == "resistant"

    arrow = current["equipment:arrow"]
    assert arrow.silver_construction == "arrowhead"
    assert arrow.price_for("solid-silver") == 40
    assert arrow.price_for("silver-coated") == 6
    for unsupported in ("equipment:bolt", "equipment:wooden-stake", "equipment:chainsaw"):
        assert current[unsupported].silver_construction is None
        with pytest.raises(ValidationError, match="cannot use silver"):
            current[unsupported].price_for("solid-silver")


def test_attack_identity_uses_weapon_or_loaded_arrow_and_fails_closed() -> None:
    sword = Item(
        id="sword",
        definition_id="equipment:broadsword",
        owner_id="a",
        silver_construction="silver-coated",
    )
    bow = Item(id="bow", definition_id="equipment:longbow", owner_id="a")
    arrow = Item(
        id="arrows",
        definition_id="equipment:arrow",
        owner_id="a",
        quantity=3,
        silver_construction="solid-silver",
    )
    state = ResourceState(
        items=(sword, bow, arrow),
        ammunition_loads=(
            AmmunitionLoad(weapon_id="bow", mode_id="shot", ammunition_item_id="arrows", rounds=1),
        ),
    )
    bow_mode = next(
        mode for mode in entries()["equipment:longbow"].modes if isinstance(mode, RangedMode)
    )
    assert attack_construction(state, "sword") == "silver-coated"
    assert attack_construction(state, "bow", bow_mode) == "solid-silver"
    with pytest.raises(ValidationError, match="absent"):
        attack_construction(state, "missing")


def test_custody_split_persistence_cas_and_receipts() -> None:
    engine = resource_engine()
    state = ResourceState(
        owners=(Owner(actor_id="a", capacity=100_000), Owner(actor_id="b", capacity=100_000)),
        items=(
            Item(
                id="sword",
                definition_id="equipment:broadsword",
                owner_id="a",
                silver_construction="solid-silver",
                condition=ObjectCondition(
                    hp=entries()["equipment:broadsword"].durability.hp  # type: ignore[union-attr]
                ),
            ),
            Item(
                id="arrows",
                definition_id="equipment:arrow",
                owner_id="a",
                quantity=4,
                silver_construction="silver-coated",
            ),
        ),
    )
    give = Transfer(
        id="give-sword",
        actor_id="a",
        expected_revision=0,
        item_id="sword",
        quantity=1,
        owner_id="b",
    )
    transferred = engine.apply(state, give)
    assert engine.apply(transferred, give) == transferred
    moved = next(item for item in transferred.items if item.id == "sword")
    assert moved.owner_id == "b" and moved.silver_construction == "solid-silver"
    assert transferred.receipts[-1].command_id == "give-sword"
    restarted = ResourceState.model_validate_json(transferred.model_dump_json())
    split = Transfer(
        id="give-arrows",
        actor_id="a",
        expected_revision=1,
        item_id="arrows",
        quantity=2,
        owner_id="b",
        new_item_id="arrows-b",
    )
    split_state = engine.apply(restarted, split)
    assert (
        next(item for item in split_state.items if item.id == "arrows-b").silver_construction
        == "silver-coated"
    )
    with pytest.raises(ConflictError, match="revision"):
        engine.apply(split_state, split.model_copy(update={"id": "stale", "expected_revision": 0}))

    invalid = state.model_copy(
        update={
            "items": (
                Item(
                    id="bolt",
                    definition_id="equipment:bolt",
                    owner_id="a",
                    silver_construction="solid-silver",
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="cannot use silver"):
        engine.validate(invalid)


@pytest.mark.parametrize(
    ("construction", "expected"),
    [(None, 5), ("silver-coated", 15), ("solid-silver", 20)],
)
def test_silver_vulnerability_wounding_and_replay(
    construction: SilverConstruction | None, expected: int
) -> None:
    target = AttackDefenseTraits(
        entries=(
            PurchasedAttackDefenseTrait(
                definition_id="disadvantage:vulnerability",
                levels=1,
                parameters=(("source", "silver"), ("rarity", "rare"), ("multiplier", 4)),
            ),
        )
    )
    multiplier = silver_wounding_multiplier(target.injury_multiplier("silver"), construction)
    state = ResourceState(
        pools=(
            Pool(id="hp:target", current=100, maximum=100, injury=InjuryStatus(profile_id=PROFILE)),
        )
    )
    command = Wound(
        id="silver-hit",
        actor_id="target",
        expected_revision=0,
        basic_damage=8,
        resistance=3,
        damage_type="cr",
        vulnerability_multiplier=multiplier,
    )
    result_state, result = apply_injury(state, command, ht=12, rng=RecordedDice([]), system=True)
    assert (result.penetration, result.injury) == (5, expected)
    assert apply_injury(result_state, command, ht=12, rng=RecordedDice([]), system=True) == (
        result_state,
        result,
    )
    assert ResourceState.model_validate_json(result_state.model_dump_json()) == result_state


def test_invalid_multiplier_and_tl0_construction_fail_closed() -> None:
    with pytest.raises(SchemaError, match="printed wounding multiplier"):
        Wound(
            id="bad",
            actor_id="target",
            expected_revision=0,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
            vulnerability_multiplier=Decimal("1.25"),
        )
    engine = resource_engine(technology_level=0)
    state = ResourceState(
        owners=(Owner(actor_id="a", capacity=10_000),),
        items=(
            Item(
                id="arrow",
                definition_id="equipment:arrow",
                owner_id="a",
                silver_construction="solid-silver",
            ),
        ),
    )
    with pytest.raises(ValidationError, match="TL1"):
        engine.validate(state)
