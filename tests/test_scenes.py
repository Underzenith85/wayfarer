"""Multi-scene travel, alternate discovery, trigger, and replay contracts."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_actions import Dice, actor_setup, campaign, engine, resource_seed, world

from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import Inspect
from wayfarer.engine.simulation.scenes import (
    Discovery,
    Obstacle,
    Scene,
    SceneExit,
    SceneRules,
    SceneTrigger,
)
from wayfarer.engine.world import Fact, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import ObserveScene, SceneService, TravelScene
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def configured() -> tuple[ActionEngine, World]:
    base = engine()
    expanded = replace(
        world(),
        facts=world().facts
        + (
            Fact("dock-seen", "dock", "visited", "yes"),
            Fact("alley-seen", "alley", "visited", "yes"),
            Fact("shortcut", "alley", "route", "known"),
        ),
    )
    scenes = SceneRules(
        id="dock-scenes",
        version=1,
        scenes=(
            Scene(
                id="dock-scene",
                version=1,
                location_id="dock",
                title="The Dock",
                exits=(
                    SceneExit(id="to-alley", destination_id="alley-scene", ticks=2),
                    SceneExit(
                        id="shortcut-to-alley",
                        destination_id="alley-scene",
                        required_fact_ids=("shortcut",),
                    ),
                ),
            ),
            Scene(
                id="alley-scene",
                version=1,
                location_id="alley",
                title="The Alley",
                exits=(SceneExit(id="return", destination_id="dock-scene"),),
                obstacles=(
                    Obstacle(
                        id="locked-return",
                        exit_id="return",
                        description="A locked gate",
                        bypass_fact_ids=("clue",),
                    ),
                ),
            ),
        ),
        discoveries=(
            Discovery(
                id="notice-dock", scene_id="dock-scene", fact_id="dock-seen", mode="automatic"
            ),
            Discovery(
                id="find-letter",
                scene_id="dock-scene",
                fact_id="clue",
                mode="check",
                target_id="chest",
            ),
            Discovery(
                id="notice-alley", scene_id="alley-scene", fact_id="alley-seen", mode="automatic"
            ),
        ),
        triggers=(
            SceneTrigger(id="enter-dock", scene_id="dock-scene", phase="entry", fact_id="shortcut"),
            SceneTrigger(id="exit-dock", scene_id="dock-scene", phase="exit", fact_id="dock-seen"),
        ),
    )
    reducer = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"scenes": scenes})
    )
    return reducer, expanded


async def setup(tmp_path: Path) -> tuple[str, PlayService, SceneService]:
    reducer, expanded = configured()
    play = PlayService(AsyncSQLiteStore(tmp_path / "scenes.sqlite", 10), reducer, rng=Dice())
    initial = campaign(reducer)
    await play.create(initial, expanded, resource_seed(), (actor_setup(),))
    return initial["id"], play, SceneService(play)


async def test_initial_observation_check_discovery_alternate_route_revisit_and_replay(
    tmp_path: Path,
) -> None:
    cid, play, scenes = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    assert state.actor_scenes[0].scene_id == "dock-scene"
    assert {entry.fact_id for entry in scenes.journal(state, "a")} == {"dock-seen"}
    # A successful authored check records evidence in the perspective journal.
    action = Inspect(id="inspect", actor_id="a", expected_revision=0, target_id="chest")
    result = await play.execute(cid, action, authenticated_actor_id="a")
    assert result.revealed_fact_ids == ("clue",)
    state = play._load(await play.store.read(cid))
    assert {entry.fact_id for entry in scenes.journal(state, "a")} == {"dock-seen", "clue"}
    moved = await scenes.execute(
        cid,
        TravelScene(id="out", actor_id="a", expected_revision=1, exit_id="shortcut-to-alley"),
        authenticated_actor_id="a",
    )
    assert moved.scene_id == "alley-scene" and "alley-seen" in moved.revealed_fact_ids
    returned = await scenes.execute(
        cid,
        TravelScene(id="back", actor_id="a", expected_revision=2, exit_id="return"),
        authenticated_actor_id="a",
    )
    assert returned.scene_id == "dock-scene"
    state = play._load(await play.store.read(cid))
    assert state.fired_scene_triggers.count("enter-dock") == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_blocked_invalid_and_retry_commands_do_not_duplicate_triggers(tmp_path: Path) -> None:
    cid, play, scenes = await setup(tmp_path)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Unknown exit"):
        await scenes.execute(
            cid,
            TravelScene(id="bad", actor_id="a", expected_revision=0, exit_id="missing"),
            authenticated_actor_id="a",
        )
    with pytest.raises(ValidationError, match="authorized"):
        await scenes.execute(
            cid,
            ObserveScene(id="forged", actor_id="a", expected_revision=0),
            authenticated_actor_id="b",
        )
    assert await play.store.read(cid) == before
    command = TravelScene(id="out", actor_id="a", expected_revision=0, exit_id="to-alley")
    first = await scenes.execute(cid, command, authenticated_actor_id="a")
    second = await scenes.execute(cid, command, authenticated_actor_id="a")
    assert first == second
    state = play._load(await play.store.read(cid))
    assert state.fired_scene_triggers.count("exit-dock") == 1
    with pytest.raises(ConflictError, match="blocked"):
        await scenes.execute(
            cid,
            TravelScene(id="blocked", actor_id="a", expected_revision=1, exit_id="return"),
            authenticated_actor_id="a",
        )
