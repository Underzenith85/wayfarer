"""Combat lifecycle, tactical action economy and persisted defense pauses."""

import asyncio
import os
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_actions import actor_setup, campaign, engine, world

from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


def combat_engine() -> ActionEngine:
    base = engine()
    rules = CombatRules(
        id="combat",
        version=1,
        movement_allowance=3,
        prone_movement_allowance=1,
        battlefields=(
            Battlefield(
                id="dock-fight",
                location_id="dock",
                width=6,
                height=6,
                blocked=(GridPoint(x=3, y=3),),
            ),
        ),
    )
    return ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"combat": rules})
    )


def resources(*, a_ready: bool = True) -> ResourceState:
    return ResourceState(
        items=(
            Item(
                id="sword-a",
                definition_id="sword",
                owner_id="a",
                equipped=True,
                ready=a_ready,
            ),
            Item(
                id="sword-b",
                definition_id="sword",
                owner_id="b",
                equipped=True,
                ready=True,
            ),
        ),
        owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
    )


def start(*, revision: int = 0) -> StartEncounter:
    return StartEncounter(
        id="start",
        actor_id="gm",
        expected_revision=revision,
        encounter_id="fight",
        battlefield_id="dock-fight",
        placements=(
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=2, y=0), facing="west"),
        ),
    )


async def setup(
    tmp_path: Path, *, backend: str = "sqlite", a_ready: bool = True
) -> tuple[str, PlayService, CombatService]:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "combat.sqlite", 10)
    reducer = combat_engine()
    play = PlayService(store, reducer)
    initial = campaign(reducer)
    actor_a = actor_setup()
    actor_b = actor_a.model_copy(update={"actor_id": "b"})
    await play.create(initial, world(), resources(a_ready=a_ready), (actor_a, actor_b))
    return initial["id"], play, CombatService(play)


def current(state: PlayState) -> Encounter:
    return state.encounters[0]


def test_battlefield_and_combat_rules_reject_invalid_environment() -> None:
    with pytest.raises(SchemaError, match="outside"):
        Battlefield(id="bad", location_id="dock", width=2, height=2, blocked=(GridPoint(x=2, y=0),))
    valid = Battlefield(id="same", location_id="dock", width=2, height=2)
    with pytest.raises(SchemaError, match="Duplicate battlefield"):
        CombatRules(id="bad", version=1, battlefields=(valid, valid))


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_full_turn_order_movement_defense_pause_restart_and_replay(
    tmp_path: Path, backend: str
) -> None:
    cid, play, service = await setup(tmp_path, backend=backend)
    results = await asyncio.gather(
        *(service.execute(cid, start(), authenticated_actor_id="gm") for _ in range(8))
    )
    assert all(result == results[0] for result in results)
    assert results[0].code == "combat.started" and results[0].current_actor_id == "a"
    assert results[0].available == (
        "do_nothing",
        "move",
        "ready",
        "change_posture",
        "attack",
        "wait",
    )
    assert len(await play.store.history(cid)) == 1
    with pytest.raises(ConflictError, match="out of turn"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="early", actor_id="b", expected_revision=1, encounter_id="fight", maneuver="wait"
            ),
            authenticated_actor_id="b",
        )
    moved = await service.execute(
        cid,
        TakeCombatTurn(
            id="move",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=1, y=0),
            facing="south",
        ),
        authenticated_actor_id="a",
    )
    assert moved.current_actor_id == "b"
    pending = await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            target_id="a",
            item_id="sword-b",
        ),
        authenticated_actor_id="b",
    )
    assert pending.code == "combat.defense_required"
    assert pending.current_actor_id == "b" and pending.available == ("dodge", "parry", "none")
    with pytest.raises(ConflictError, match="cannot accept"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="skip", actor_id="b", expected_revision=3, encounter_id="fight", maneuver="wait"
            ),
            authenticated_actor_id="b",
        )
    restarted = CombatService(PlayService(play.store, play.engine))
    chosen = await restarted.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            defense="parry",
        ),
        authenticated_actor_id="a",
    )
    assert chosen.code == "combat.defense_recorded"
    assert chosen.current_actor_id == "a" and chosen.round == 2
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    encounter = current(state)
    assert encounter.pending_defense is None
    assert encounter.defense_history[0].selected == "parry"
    combatant = next(p for p in encounter.participants if p.actor_id == "a")
    assert combatant.position == GridPoint(x=1, y=0) and combatant.facing == "south"
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert len(await play.store.history(cid)) == 4


async def test_action_economy_posture_terrain_reach_and_parameter_validation(
    tmp_path: Path,
) -> None:
    cid, play, service = await setup(tmp_path)
    await service.execute(cid, start(), authenticated_actor_id="gm")
    before = await play.store.read(cid)
    invalid_moves = (
        GridPoint(x=4, y=0),
        GridPoint(x=3, y=3),
        GridPoint(x=2, y=0),
    )
    for index, point in enumerate(invalid_moves):
        with pytest.raises(ValidationError, match="movement"):
            await service.execute(
                cid,
                TakeCombatTurn(
                    id=f"bad-{index}",
                    actor_id="a",
                    expected_revision=1,
                    encounter_id="fight",
                    maneuver="move",
                    destination=point,
                ),
                authenticated_actor_id="a",
            )
    with pytest.raises(ValidationError, match="unexpected"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="bad-wait",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                maneuver="wait",
                target_id="b",
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before
    await service.execute(
        cid,
        TakeCombatTurn(
            id="prone",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="change_posture",
            posture="prone",
        ),
        authenticated_actor_id="a",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="b-waits",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="wait",
        ),
        authenticated_actor_id="b",
    )
    with pytest.raises(ValidationError, match="movement"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="crawl-too-far",
                actor_id="a",
                expected_revision=3,
                encounter_id="fight",
                maneuver="move",
                destination=GridPoint(x=2, y=0),
            ),
            authenticated_actor_id="a",
        )
    with pytest.raises(ValidationError, match="out of reach"):
        await service.execute(
            cid,
            TakeCombatTurn(
                id="far-attack",
                actor_id="a",
                expected_revision=3,
                encounter_id="fight",
                maneuver="attack",
                target_id="b",
                item_id="sword-a",
            ),
            authenticated_actor_id="a",
        )


async def test_combat_blocks_ordinary_actions_and_lifecycle_requires_gm(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    with pytest.raises(ValidationError, match="GM authority"):
        await service.execute(
            cid, start().model_copy(update={"actor_id": "a"}), authenticated_actor_id="a"
        )
    await service.execute(cid, start(), authenticated_actor_id="gm")
    ordinary = await play.execute(
        cid,
        Wait(id="ordinary", actor_id="a", expected_revision=1, ticks=1),
        authenticated_actor_id="a",
    )
    assert ordinary.code == "combat.command_required"
    assert len(await play.store.history(cid)) == 1
    with pytest.raises(ValidationError, match="GM authority"):
        await service.execute(
            cid,
            EndEncounter(
                id="illegal-end",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                reason="No",
            ),
            authenticated_actor_id="a",
        )
    completed = await service.execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=1,
            encounter_id="fight",
            reason="Opponents withdrew",
        ),
        authenticated_actor_id="gm",
    )
    assert completed.code == "combat.completed" and completed.available == ()
    ordinary = await play.execute(
        cid,
        Wait(id="ordinary", actor_id="a", expected_revision=2, ticks=1),
        authenticated_actor_id="a",
    )
    assert ordinary.status == "committed"


async def test_ready_maneuver_syncs_inventory_and_rejects_forged_state(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path, a_ready=False)
    await service.execute(cid, start(), authenticated_actor_id="gm")
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    forged = current(state).model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"ready_item_ids": ("sword-a",)}) if p.actor_id == "a" else p
                for p in current(state).participants
            )
        }
    )
    with pytest.raises(ValidationError, match="readiness"):
        play.engine.validate(state.model_copy(update={"encounters": (forged,)}))
    # The item starts unready. The flag is changed through the inventory reducer,
    # then the combat projection is synchronized in the same transaction.
    result = await service.execute(
        cid,
        TakeCombatTurn(
            id="ready",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="ready",
            item_id="sword-a",
        ),
        authenticated_actor_id="a",
    )
    assert result.current_actor_id == "b"
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    assert "sword-a" in next(
        p.ready_item_ids for p in current(state).participants if p.actor_id == "a"
    )
    assert next(item.ready for item in state.resources.items if item.id == "sword-a")


async def test_start_rejects_identity_placement_and_configuration_forgery(tmp_path: Path) -> None:
    cid, play, service = await setup(tmp_path)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, start(), authenticated_actor_id="a")
    for placements in (
        start().placements[:1],
        start().placements + (start().placements[0],),
        (
            Placement(actor_id="a", position=GridPoint(x=0, y=0)),
            Placement(actor_id="b", position=GridPoint(x=0, y=0)),
        ),
    ):
        with pytest.raises((ValidationError, SchemaError)):
            await service.execute(
                cid,
                start().model_copy(update={"id": "changed", "placements": placements}),
                authenticated_actor_id="gm",
            )
    assert await play.store.read(cid) == before
