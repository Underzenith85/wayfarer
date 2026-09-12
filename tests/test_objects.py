"""Independent B380, B483-484 numeric facts and authoritative custody checks."""

import asyncio
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_resources import campaign, engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.equipment.objects import (
    DamageObject,
    StressObject,
    apply_object,
    initialize_object,
    object_hp,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Advance, Equip, ResourceState, Transfer, Unequip
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


def fixture(construction: str = "homogenous") -> tuple[ResourceEngine, ResourceState]:
    reducer, state = engine(), seed()
    profile = ObjectProfile.model_validate(
        {"construction": construction, "hp": 10, "dr": 2, "ht": 12}
    )
    reducer.specs["sword"] = reducer.specs["sword"].model_copy(update={"durability": profile})
    state = state.model_copy(
        update={
            "items": tuple(
                initialize_object(i, profile) if i.id == "sword" else i for i in state.items
            )
        }
    )
    return reducer, state


def hit(damage: int, **kwargs: object) -> DamageObject:
    return DamageObject.model_validate(
        {
            "id": "hit",
            "actor_id": "a",
            "expected_revision": 0,
            "item_id": "sword",
            "basic_damage": damage,
            "damage_type": "cr",
            **kwargs,
        }
    )


@pytest.mark.parametrize(
    "weight,kind,expected",
    [
        (1000, "homogenous", 8),
        (3000, "homogenous", 12),
        (8000, "homogenous", 16),
        (1000, "unliving", 4),
        (27000, "unliving", 12),
    ],
)
def test_hp_source_examples(weight: int, kind: str, expected: int) -> None:
    assert (
        object_hp(
            weight,
            ObjectProfile.model_validate(
                {"construction": kind, "hp": 1, "dr": 0, "ht": 10}
            ).construction,
        )
        == expected
    )


@given(st.integers(1, 1000000000))
def test_hp_is_smallest_integer_cube_bound(weight: int) -> None:
    hp = object_hp(weight, "homogenous")
    assert (hp - 1) ** 3 * 1000 < 512 * weight <= hp**3 * 1000


@pytest.mark.parametrize(
    "construction,kind,damage,expected",
    [
        ("homogenous", "pi-", 11, 1),
        ("homogenous", "pi", 12, 2),
        ("homogenous", "pi+", 11, 3),
        ("homogenous", "imp", 10, 4),
        ("unliving", "pi", 10, 2),
        ("unliving", "pi-", 12, 2),
        ("unliving", "imp", 7, 5),
        ("homogenous", "cut", 7, 7),
        ("homogenous", "cr", 2, 0),
        ("diffuse", "imp", 102, 1),
        ("diffuse", "pi++", 102, 1),
        ("diffuse", "cut", 102, 2),
        ("diffuse", "burn", 102, 2),
    ],
)
def test_independent_wounding(construction: str, kind: str, damage: int, expected: int) -> None:
    reducer, state = fixture(construction)
    updated, result = apply_object(
        reducer, state, hit(damage, damage_type=kind), system=True, rng=RecordedDice([])
    )
    assert result.injury == expected
    assert result.condition.hp == 10 - expected
    assert updated.revision == 1


def test_zero_hp_requires_stress_not_automatic_breakage_and_retries() -> None:
    reducer, state = fixture()
    state, result = apply_object(reducer, state, hit(12), system=True, rng=RecordedDice([]))
    assert result.condition.hp == 0 and not result.condition.disabled
    command = StressObject(id="stress", actor_id="a", expected_revision=1, item_id="sword")
    state, result = apply_object(reducer, state, command, system=True, rng=RecordedDice([5, 5, 5]))
    assert result.condition.disabled and not result.condition.destroyed
    restored = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_object(reducer, restored, command, system=True, rng=RecordedDice([])) == (
        state,
        result,
    )
    with pytest.raises(ValidationError, match="Disabled"):
        reducer.apply(state, Equip(id="equip", actor_id="a", expected_revision=2, item_id="sword"))
    transferred = reducer.apply(
        state,
        Transfer(
            id="trade", actor_id="a", expected_revision=2, item_id="sword", quantity=1, owner_id="b"
        ),
    )
    item = next(i for i in transferred.items if i.id == "sword")
    assert item.owner_id == "b" and item.condition == result.condition
    assert reducer.carried_weight(transferred, "b") == 5


def test_multiple_thresholds_and_automatic_destruction() -> None:
    reducer, state = fixture()
    _, result = apply_object(reducer, state, hit(42), system=True, rng=RecordedDice([3, 3, 3] * 3))
    assert result.condition.hp == -30 and len(result.checks) == 3
    assert not result.condition.destroyed
    _, result = apply_object(reducer, state, hit(22), system=True, rng=RecordedDice([6, 6, 6]))
    assert result.condition.destroyed
    _, result = apply_object(reducer, state, hit(62), system=True, rng=RecordedDice([]))
    assert result.condition.destroyed and not result.checks


def test_stress_once_per_second_and_authority_before_rng() -> None:
    reducer, state = fixture()
    with pytest.raises(ValidationError, match="authority"):
        apply_object(reducer, state, hit(20), rng=RecordedDice([]))
    state, _ = apply_object(reducer, state, hit(12), system=True, rng=RecordedDice([]))
    stress = StressObject(id="stress", actor_id="a", item_id="sword", expected_revision=1)
    state, _ = apply_object(reducer, state, stress, system=True, rng=RecordedDice([3, 3, 3]))
    with pytest.raises(ConflictError, match="second"):
        apply_object(
            reducer,
            state,
            stress.model_copy(update={"id": "again", "expected_revision": 2}),
            system=True,
            rng=RecordedDice([]),
        )
    state = reducer.apply(
        state, Advance(id="clock", actor_id="a", expected_revision=2, to=1), system=True
    )
    state, _ = apply_object(
        reducer,
        state,
        stress.model_copy(update={"id": "next", "expected_revision": 3}),
        system=True,
        rng=RecordedDice([5, 5, 5]),
    )
    assert next(i for i in state.items if i.id == "sword").condition == ObjectCondition(
        hp=0, disabled=True, last_stress_at=1, shock=4, shock_until=1
    )


def test_ready_equipment_loses_effects_but_keeps_custody() -> None:
    reducer, state = fixture()
    state = reducer.apply(
        state, Equip(id="equip", actor_id="a", expected_revision=0, item_id="sword")
    )
    state, _ = apply_object(
        reducer, state, hit(62, expected_revision=1), system=True, rng=RecordedDice([])
    )
    item = next(i for i in state.items if i.id == "sword")
    assert item.equipped and not item.ready and item.owner_id == "a"
    assert reducer.carried_weight(state, "a") == 17
    state = reducer.apply(
        state, Unequip(id="off", actor_id="a", expected_revision=2, item_id="sword")
    )
    assert next(i for i in state.items if i.id == "sword").condition == item.condition


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_damage_atomic_retry_restart_and_custody_history(
    tmp_path: Path, backend: str
) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "objects.sqlite", 10)
    reducer, state = fixture()
    service = ResourceService(store, reducer)
    initial = campaign(reducer)
    await service.create(initial, state)
    cid = initial["id"]
    command = hit(22)
    results = await asyncio.gather(
        *(
            service.execute_object(
                cid, command, authenticated_actor_id="a", system=True, rng=RecordedDice([5, 5, 5])
            )
            for _ in range(4)
        )
    )
    assert all(r == results[0] for r in results) and results[0].revision == 1
    restarted = ResourceService(store, reducer)
    assert (
        await restarted.execute_object(
            cid, command, authenticated_actor_id="a", system=True, rng=RecordedDice([])
        )
        == results[0]
    )
    with pytest.raises(ValidationError, match="authority"):
        await restarted.execute_object(
            cid, command, authenticated_actor_id="b", system=True, rng=RecordedDice([])
        )
    with pytest.raises(ConflictError):
        await restarted.execute_object(
            cid,
            command.model_copy(update={"basic_damage": 23}),
            authenticated_actor_id="a",
            system=True,
            rng=RecordedDice([]),
        )
    await restarted.execute(
        cid,
        Transfer(
            id="trade", actor_id="a", expected_revision=1, item_id="sword", quantity=1, owner_id="b"
        ),
        authenticated_actor_id="a",
    )
    assert len(await store.history(cid)) == 2
    assert await store.replay(cid) == await store.read(cid)


def test_profile_initialization_does_not_migrate_or_reset_items() -> None:
    reducer, state = fixture()
    item = next(i for i in state.items if i.id == "sword")
    profile = reducer.specs["sword"].durability
    assert profile is not None
    with pytest.raises(ValidationError, match="uninitialized"):
        initialize_object(item, profile)
    with pytest.raises(ValidationError, match="initialized"):
        reducer.validate(seed())
    with pytest.raises(ValidationError, match="pinned"):
        engine().validate(state)
    with pytest.raises(ValidationError, match="positive weight"):
        object_hp(0, "homogenous")


@pytest.mark.parametrize("divisor,expected_dr,injury", [("2", 1, 4), ("0.5", 4, 1)])
def test_object_armor_divisors(divisor: str, expected_dr: int, injury: int) -> None:
    from decimal import Decimal

    reducer, state = fixture()
    _, result = apply_object(
        reducer, state, hit(5, armor_divisor=Decimal(divisor)), system=True, rng=RecordedDice([])
    )
    assert (result.effective_dr, result.injury) == (expected_dr, injury)


async def test_resource_only_damage_refuses_live_play_state(tmp_path: Path) -> None:
    reducer, state = fixture()
    service = ResourceService(AsyncSQLiteStore(tmp_path / "live.sqlite"), reducer)
    initial = campaign(reducer)
    initial["play_json"] = "{}"
    await service.create(initial, state)
    with pytest.raises(ValidationError, match="combat transaction"):
        await service.execute_object(
            initial["id"], hit(12), authenticated_actor_id="a", system=True, rng=RecordedDice([])
        )
    assert (await service.store.read(initial["id"]))["revision"] == 0
