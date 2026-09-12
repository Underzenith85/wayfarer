"""Template ownership, explicit retained-checkpoint migration, and replay."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_tactical import migration, setup

from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError
from wayfarer.orchestration.battlefield_templates import migrate_embedded_maps
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def test_embedded_map_migrates_then_replays_after_cache_loss(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path / "source", migrate=False)
    initial = await play.store.read(cid)
    raw = json.loads(initial["play_json"])
    encounter = raw["encounters"][0]
    command = migration()
    context = encounter.pop("spatial_context")
    encounter["battlefield_id"] = context["battlefield_id"]
    encounter["hex_battlefield"] = command.battlefield.model_dump(
        mode="json", exclude={"location_id", "darkness_penalty"}
    )
    poses = {p.actor_id: p.pose for p in command.placements}
    for actor in encounter["participants"]:
        actor["position"] = poses[actor["actor_id"]].position.model_dump(mode="json")
        actor["hex_facing"] = poses[actor["actor_id"]].facing
    initial["play_json"] = json.dumps(raw)
    store = AsyncSQLiteStore(tmp_path / "legacy.sqlite", snapshot_interval=0)
    await store.insert(initial)
    legacy = PlayService(store, play.engine)
    with pytest.raises(SchemaError):
        legacy._load(initial)
    migrated = await migrate_embedded_maps(
        legacy, cid, command_id="lift", actor_id="gm", expected_revision=initial["revision"]
    )
    bound = legacy.for_campaign(migrated)
    state = bound._load(migrated)
    assert "hex_battlefield" not in Encounter.model_fields
    assert state.encounters[0].spatial_kind == "hex"
    board = bound.rules_context.require_hex(state.encounters[0])
    assert board.cells == command.battlefield.cells
    assert board.location_id == "dock"
    assert board.source_template_id == "dock"
    assert state.migrations[-1].from_digest == play.engine.digest
    assert state.migrations[-1].to_digest == bound.engine.digest
    assert state.migrations[-1].revision == initial["revision"] + 1
    assert document(await store.replay(cid)) == document(migrated)
    retried = await migrate_embedded_maps(
        legacy, cid, command_id="lift", actor_id="gm", expected_revision=initial["revision"]
    )
    assert document(retried) == document(migrated)
    record = (await store.history(cid))[-1]
    replay_store = AsyncSQLiteStore(tmp_path / "reexecute.sqlite", snapshot_interval=0)
    await replay_store.insert(initial)
    await execute_recorded(PlayService(replay_store, play.engine), record)
    assert document(await replay_store.read(cid)) == document(migrated)
    assert [e.event for e in await replay_store.stream(cid)] == [
        e.event for e in await store.stream(cid)
    ]


async def test_missing_template_fails_closed_and_geometry_is_pinned(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    encounter = state.encounters[0]
    context = encounter.spatial
    assert context.kind == "hex"
    broken = encounter.model_copy(
        update={"spatial_context": context.model_copy(update={"battlefield_id": "missing"})}
    )
    with pytest.raises(ValidationError, match="battlefield|template"):
        play.engine.validate(state.model_copy(update={"encounters": (broken,)}))
    rules = play.engine.rules.combat
    assert rules is not None
    board = play.rules_context.require_hex(encounter)
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine

    modified = board.model_copy(update={"darkness_penalty": -1})
    combat = rules.model_copy(
        update={
            "battlefields": tuple(modified if b.id == board.id else b for b in rules.battlefields)
        }
    )
    engine = ActionEngine(
        play.engine.reviewer,
        play.engine.resources,
        play.engine.rules.model_copy(update={"combat": combat}),
    )
    assert engine.digest != play.engine.digest
    assert any(isinstance(b, HexBattlefield) for b in rules.battlefields)


async def test_hex_template_scene_location_is_checked(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene
    from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules

    cid, play = await setup(tmp_path)
    encounter = play._load(await play.store.read(cid)).encounters[0]
    rules = play.engine.rules.combat
    assert rules is not None
    scenes = SceneRules(
        id="scenes",
        version=1,
        scenes=(
            Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),
            Scene(id="wrong-scene", version=1, location_id="alley", title="Alley"),
        ),
    )
    assert bind_scene(encounter, scenes, rules, "dock-scene").scene_id == "dock-scene"
    with pytest.raises(ValidationError, match="location"):
        bind_scene(encounter, scenes, rules, "wrong-scene")
