"""Resource conservation, scheduling and durable transaction contracts."""

import asyncio
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as SchemaError

from wayfarer.character import builder
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    reference,
)
from wayfarer.simulation.resources import (
    COMMAND_ADAPTER,
    Advance,
    Consume,
    Equip,
    EquipmentSpec,
    Item,
    Owner,
    Pool,
    ResourceEngine,
    ResourceState,
    Schedule,
    Scheduled,
    Transfer,
    Unequip,
)
from wayfarer.simulation.scenario import scenario
from wayfarer.world import Entity, EntityKind, World


def engine() -> ResourceEngine:
    package = replace(
        PROTOTYPE_PACKAGE,
        definitions=PROTOTYPE_PACKAGE.definitions
        + tuple(
            RuleDefinition(
                key,
                DefinitionKind.EQUIPMENT,
                key,
                PROTOTYPE_SOURCE.id,
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for key in ("arrow", "bag", "sword")
        ),
    )
    policy = replace(DEFAULT_POLICY, allowed_equipment=frozenset({"arrow", "bag", "sword"}))
    rules = replace(
        DEFAULT_RULES, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    world = World(entities=(Entity("a", EntityKind.ACTOR, "A"), Entity("b", EntityKind.ACTOR, "B")))
    return ResourceEngine(
        world,
        RulesCatalog((package,)),
        rules,
        policy,
        (
            EquipmentSpec(definition_id="arrow", unit_weight=1, ammunition=True),
            EquipmentSpec(definition_id="bag", unit_weight=2, container_capacity=20),
            EquipmentSpec(
                definition_id="sword",
                unit_weight=5,
                stackable=False,
                slot="hand",
                required_definitions=("skill:stealth",),
            ),
        ),
    )


def seed(quantity: int = 10) -> ResourceState:
    return ResourceState(
        items=(
            Item(id="arrows", definition_id="arrow", owner_id="a", quantity=quantity),
            Item(id="bag", definition_id="bag", owner_id="a"),
            Item(id="sword", definition_id="sword", owner_id="a"),
        ),
        owners=(
            Owner(actor_id="a", capacity=200, definitions=("skill:stealth",)),
            Owner(actor_id="b", capacity=100),
        ),
        pools=(Pool(id="hp:a", current=5, maximum=10),),
        active_effect_ids=("poison",),
    )


def test_split_transfer_consume_and_exact_retry() -> None:
    reducer, state = engine(), seed()
    command = Transfer(
        id="split",
        actor_id="a",
        expected_revision=0,
        item_id="arrows",
        quantity=3,
        owner_id="b",
        new_item_id="b-arrows",
    )
    result = reducer.apply(state, command)
    assert sum(i.quantity for i in result.items if i.definition_id == "arrow") == 10
    assert reducer.carried_weight(result, "a") == 14
    assert reducer.apply(result, command) == result
    restored = ResourceState.model_validate_json(result.model_dump_json())
    assert restored == result and reducer.apply(restored, command) == restored
    consumed = reducer.apply(
        result,
        Consume(
            id="consume",
            actor_id="b",
            expected_revision=1,
            item_id="b-arrows",
            quantity=3,
            require_ammunition=True,
        ),
    )
    assert sum(i.quantity for i in consumed.items if i.definition_id == "arrow") == 7
    assert reducer.apply(consumed, command) == consumed
    assert state == seed()
    with pytest.raises(ConflictError):
        reducer.apply(result, command.model_copy(update={"quantity": 2}))
    with pytest.raises(ConflictError):
        reducer.apply(
            result,
            Consume(id="stale", actor_id="a", expected_revision=0, item_id="arrows", quantity=1),
        )


@pytest.mark.parametrize("amount", [True, "1", 1.5, 0, -1])
def test_command_boundary_rejects_invalid_quantity(amount: object) -> None:
    with pytest.raises(SchemaError):
        COMMAND_ADAPTER.validate_python(
            dict(
                kind="consume",
                id="x",
                actor_id="a",
                expected_revision=0,
                item_id="arrows",
                quantity=amount,
            )
        )


def test_authority_availability_equipment_and_capacity() -> None:
    reducer, state = engine(), seed()
    with pytest.raises(ValidationError, match="owned"):
        reducer.apply(
            state, Consume(id="x", actor_id="b", expected_revision=0, item_id="arrows", quantity=1)
        )
    with pytest.raises(ValidationError, match="quantity"):
        reducer.apply(
            state, Consume(id="x", actor_id="a", expected_revision=0, item_id="arrows", quantity=11)
        )
    with pytest.raises(ValidationError, match="ammunition"):
        reducer.apply(
            state,
            Consume(
                id="x",
                actor_id="a",
                expected_revision=0,
                item_id="sword",
                quantity=1,
                require_ammunition=True,
            ),
        )
    equipped = reducer.apply(
        state, Equip(id="equip", actor_id="a", expected_revision=0, item_id="sword")
    )
    assert next(i for i in equipped.items if i.id == "sword").ready
    with pytest.raises(ValidationError, match="Unequip"):
        reducer.apply(
            equipped,
            Transfer(
                id="x", actor_id="a", expected_revision=1, item_id="sword", quantity=1, owner_id="b"
            ),
        )
    unequipped = reducer.apply(
        equipped, Unequip(id="unequip", actor_id="a", expected_revision=1, item_id="sword")
    )
    moved = reducer.apply(
        unequipped,
        Transfer(
            id="move", actor_id="a", expected_revision=2, item_id="sword", quantity=1, owner_id="b"
        ),
    )
    with pytest.raises(ValidationError, match="prerequisites"):
        reducer.apply(moved, Equip(id="x", actor_id="b", expected_revision=3, item_id="sword"))
    limited = state.model_copy(
        update={"owners": (Owner(actor_id="a", capacity=1), Owner(actor_id="b", capacity=100))}
    )
    with pytest.raises(ValidationError, match="capacity"):
        reducer.validate(limited)


def test_containers_cycle_capacity_and_accessibility() -> None:
    reducer, state = engine(), seed()
    stored = reducer.apply(
        state,
        Transfer(
            id="store",
            actor_id="a",
            expected_revision=0,
            item_id="arrows",
            quantity=10,
            owner_id="a",
            container_id="bag",
        ),
    )
    with pytest.raises(ValidationError, match="Empty"):
        reducer.apply(
            stored,
            Transfer(
                id="x", actor_id="a", expected_revision=1, item_id="bag", quantity=1, owner_id="b"
            ),
        )
    with pytest.raises(ValidationError, match="cycle"):
        reducer.apply(
            state,
            Transfer(
                id="x",
                actor_id="a",
                expected_revision=0,
                item_id="bag",
                quantity=1,
                owner_id="a",
                container_id="bag",
            ),
        )
    with pytest.raises(ValidationError, match="capacity"):
        reducer.apply(
            seed(21),
            Transfer(
                id="x",
                actor_id="a",
                expected_revision=0,
                item_id="arrows",
                quantity=21,
                owner_id="a",
                container_id="bag",
            ),
        )
    with pytest.raises(ValidationError, match="same owner"):
        reducer.apply(
            state,
            Transfer(
                id="x",
                actor_id="a",
                expected_revision=0,
                item_id="arrows",
                quantity=10,
                owner_id="b",
                container_id="bag",
            ),
        )


def test_schedule_order_recovery_expiration_once_and_restart() -> None:
    reducer, state = engine(), seed()
    for index, entry in enumerate(
        (
            Scheduled(id="z-expire", due=10, kind="expire", target_id="poison"),
            Scheduled(id="a-recover", due=10, kind="recover", target_id="hp:a", amount=20),
            Scheduled(id="later", due=20, kind="consequence", target_id="a"),
        )
    ):
        command = Schedule(
            id=f"schedule-{index}", actor_id="a", expected_revision=index, entry=entry
        )
        with pytest.raises(ValidationError, match="authority"):
            reducer.apply(state, command)
        state = reducer.apply(state, command, system=True)
    command_advance = Advance(id="advance", actor_id="a", expected_revision=3, to=10)
    state = reducer.apply(state, command_advance, system=True)
    assert state.fired == ("a-recover", "z-expire")
    assert state.active_effect_ids == () and state.pools[0].current == 10
    assert [e.at for e in state.events] == [10, 10]
    assert reducer.apply(state, command_advance, system=True) == state
    state = ResourceState.model_validate_json(state.model_dump_json())
    final = reducer.apply(
        state, Advance(id="later", actor_id="a", expected_revision=4, to=25), system=True
    )
    assert final.fired == ("a-recover", "z-expire", "later") and len(final.events) == 3
    with pytest.raises(ValidationError, match="backwards"):
        reducer.apply(
            final, Advance(id="back", actor_id="a", expected_revision=5, to=1), system=True
        )
    with pytest.raises(ValidationError, match="already used"):
        reducer.apply(
            final,
            Schedule(
                id="again",
                actor_id="a",
                expected_revision=5,
                entry=Scheduled(id="later", due=30, kind="consequence", target_id="a"),
            ),
            system=True,
        )


@given(st.integers(min_value=2, max_value=100), st.data())
def test_transfer_conservation(quantity: int, data: st.DataObject) -> None:
    amount = data.draw(st.integers(min_value=1, max_value=quantity - 1))
    reducer, state = engine(), seed(quantity)
    moved = reducer.apply(
        state,
        Transfer(
            id="x",
            actor_id="a",
            expected_revision=0,
            item_id="arrows",
            quantity=amount,
            owner_id="b",
            new_item_id="split",
        ),
    )
    assert sum(i.quantity for i in moved.items if i.definition_id == "arrow") == quantity
    assert sum(reducer.carried_weight(moved, owner) for owner in ("a", "b")) == quantity + 7


def campaign(reducer: ResourceEngine) -> Campaign:
    return Campaign(
        id=str(uuid.uuid4()),
        revision=0,
        rules="test-wave6",
        rules_ref=reference(reducer.rules),
        character=builder.character(),
        scenario=scenario(),
        hp=10,
        fp=10,
        minutes=0,
        location="docks",
        inventory=[],
        discoveries=[],
        flags=[],
        complete=False,
        messages=[],
    )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_durable_concurrency_rollback_replay_and_auth(tmp_path: Path, backend: str) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "resources.sqlite", 10)
    reducer = engine()
    service = ResourceService(store, reducer)
    initial = campaign(reducer)
    await service.create(initial, seed())
    command = Consume(id="one", actor_id="a", expected_revision=0, item_id="arrows", quantity=3)
    results = await asyncio.gather(
        *(service.execute(initial["id"], command, authenticated_actor_id="a") for _ in range(8))
    )
    assert all(r == results[0] for r in results)
    assert results[0].revision == 1
    assert next(i.quantity for i in results[0].items if i.id == "arrows") == 7
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(initial["id"], command, authenticated_actor_id="b")
    with pytest.raises(ValidationError):
        await service.execute(
            initial["id"],
            Consume(id="bad", actor_id="a", expected_revision=1, item_id="arrows", quantity=8),
            authenticated_actor_id="a",
        )
    second = Consume(id="two", actor_id="a", expected_revision=1, item_id="arrows", quantity=2)
    await service.execute(initial["id"], second, authenticated_actor_id="a")
    restarted = ResourceService(store, reducer)
    assert await restarted.execute(initial["id"], command, authenticated_actor_id="a") == results[0]
    assert await store.replay(initial["id"]) == await store.read(initial["id"])
    history = await store.history(initial["id"])
    assert (
        len(history) == 2
        and history[0].actor_id == "a"
        and history[0].event["action"] == "resource"
    )
    with pytest.raises(ConflictError):
        await restarted.execute(
            initial["id"], second.model_copy(update={"id": "stale"}), authenticated_actor_id="a"
        )
    assert len(await store.history(initial["id"])) == 2
    competing = await asyncio.gather(
        *(
            restarted.execute(
                initial["id"],
                Consume(
                    id=f"race-{i}", actor_id="a", expected_revision=2, item_id="arrows", quantity=4
                ),
                authenticated_actor_id="a",
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ResourceState) for r in competing) == 1
    assert sum(isinstance(r, ConflictError) for r in competing) == 1
    await restarted.execute(
        initial["id"],
        Schedule(
            id="expiry",
            actor_id="a",
            expected_revision=3,
            entry=Scheduled(id="poison-expiry", due=10, kind="expire", target_id="poison"),
        ),
        authenticated_actor_id="a",
        system=True,
    )
    clock = Advance(id="clock", actor_id="a", expected_revision=4, to=10)
    expired = await ResourceService(store, reducer).execute(
        initial["id"], clock, authenticated_actor_id="a", system=True
    )
    assert expired.active_effect_ids == () and expired.fired == ("poison-expiry",)
    assert (
        await restarted.execute(initial["id"], clock, authenticated_actor_id="a", system=True)
        == expired
    )
    for revision in range(5, 12):
        await restarted.execute(
            initial["id"],
            Advance(
                id=f"tick-{revision}", actor_id="a", expected_revision=revision, to=revision + 10
            ),
            authenticated_actor_id="a",
            system=True,
        )
    reconstructed = await store.replay(initial["id"])
    assert reconstructed == await store.read(initial["id"])
    resources = ResourceState.model_validate_json(reconstructed["resources_json"])
    assert resources.fired == ("poison-expiry",) and len(resources.events) == 1
