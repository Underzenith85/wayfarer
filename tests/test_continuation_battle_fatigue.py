"""Generic continuation carries genuine B426 history; no Roadwatch scope expansion."""

from dataclasses import replace
from pathlib import Path
from typing import Never
from uuid import uuid4

import pytest
from support.runtime import build_play, build_runtime
from support.setup import play_game

from wayfarer.adventures import roadwatch
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.scenario_references import verify
from wayfarer.engine.simulation.campaign.setup import SetupCommand
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.battle_fatigue import PREFIX, read
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.models import Record
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import TravelScene
from wayfarer.orchestration.setup import SetupService


def starting_graph() -> ScenarioGraph:
    graph = roadwatch.adventure()
    world = replace(
        graph.world,
        entities=tuple(
            replace(entity, location_id="road") if entity.id in ("a", "b") else entity
            for entity in graph.world.entities
        ),
    )
    return graph.model_copy(update={"world": world, "opening_scene_id": "road-scene"})


async def state(play: PlayService, cid: str) -> PlayState:
    campaign = await play.store.read(cid)
    return play.for_campaign(campaign)._load(campaign)


async def submit(play: PlayService, cid: str, command: Record, *, principal_id: str) -> None:
    await build_runtime(play).submit_json(
        cid, command.model_dump(mode="json"), principal_id=principal_id
    )


async def actual_battle(play: PlayService, cid: str) -> None:
    current = await state(play, cid)
    await submit(
        play,
        cid,
        StartEncounter(
            id="battle-start",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="continued-battle",
            battlefield_id=roadwatch.BATTLEFIELD_ID,
            placements=(
                Placement(actor_id="a", position=Hex(q=2, r=0), hex_facing=0),
                Placement(actor_id="b", position=Hex(q=0, r=1), hex_facing=0),
                Placement(actor_id="brigand", position=Hex(q=3, r=0), hex_facing=3),
            ),
        ),
        principal_id="gm",
    )
    current = await state(play, cid)
    await submit(
        play,
        cid,
        TakeCombatTurn(
            id="actual-attack",
            actor_id="a",
            expected_revision=current.revision,
            encounter_id="continued-battle",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="brigand",
        ),
        principal_id="party",
    )
    current = await state(play, cid)
    if current.encounters[0].pending_defense is not None:
        await submit(
            play,
            cid,
            ChooseDefense(
                id="native-defense",
                actor_id="brigand",
                expected_revision=current.revision,
                encounter_id="continued-battle",
                defense="none",
            ),
            principal_id="gm",
        )
    current = await state(play, cid)
    assert read(current.resources, "continued-battle").rolled == ("a",)
    while read(current.resources, "continued-battle").seconds < 11:
        encounter = current.encounters[0]
        actor = encounter.current_actor_id
        await submit(
            play,
            cid,
            TakeCombatTurn(
                id=f"native-turn-{current.revision}",
                actor_id=actor,
                expected_revision=current.revision,
                encounter_id=encounter.id,
                maneuver="do_nothing",
            ),
            principal_id="gm" if actor == "brigand" else "party",
        )
        current = await state(play, cid)
    await submit(
        play,
        cid,
        EndEncounter(
            id="native-end",
            actor_id="gm",
            expected_revision=current.revision,
            encounter_id="continued-battle",
            reason="The actual ordinary battle is over",
        ),
        principal_id="gm",
    )
    current = await state(play, cid)
    ledger = read(current.resources, "continued-battle")
    assert ledger.charged and ledger.seconds == 11 and ledger.rolled == ("a",)
    assert next(pool.current for pool in current.resources.pools if pool.id == "fp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_generic_continuation_retains_native_battle_ledger_and_paid_fp(
    tmp_path: Path, backend: str
) -> None:
    # The generic setup service intentionally has no fixed Roadwatch HTTP policy.
    engine = roadwatch.engine()
    play = build_play(tmp_path, engine, backend=backend, seeds=lambda: format(1, "064x"))
    setup = SetupService(build_runtime(play))
    graph = starting_graph()
    cid = await play_game(setup, graph, command_id=str(uuid4()), principal_id="party")
    await actual_battle(play, cid)
    for actor in ("a", "b"):
        current = await state(play, cid)
        await submit(
            play,
            cid,
            TravelScene(
                id=f"native-travel-{actor}",
                actor_id=actor,
                expected_revision=current.revision,
                exit_id="road-to-shelter",
            ),
            principal_id="party",
        )
    current = await state(play, cid)
    assert current.objectives.outcome == "success"
    await setup.execute(
        cid,
        SetupCommand(id="complete", expected_revision=current.revision, operation="complete"),
        principal_id="party",
    )
    before = await play.store.read(cid)
    native = await state(play, cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    ledger_event = next(event for event in native.resources.events if event.id.startswith(PREFIX))
    successor = graph.model_copy(
        update={
            "id": "battle-history-sequel",
            "objectives": graph.objectives.model_copy(update={"id": "battle-history-objectives"}),
        }
    )
    await setup.execute(
        cid,
        SetupCommand(
            id="preview",
            expected_revision=native.revision,
            operation="preview",
            graph=successor,
        ),
        principal_id="party",
    )
    preview = await play.store.read(cid)
    saved = SetupService.load(preview).next_graph
    assert saved is not None and saved.resources == successor.resources
    assert not any(event.id.startswith(PREFIX) for event in saved.resources.events)
    assert (await state(play, cid)).resources.events == native.resources.events
    restarted_play = build_play(tmp_path, engine, backend=backend)
    restarted = SetupService(build_runtime(restarted_play))
    command = SetupCommand(
        id="continue", expected_revision=preview["revision"], operation="continue"
    )
    await restarted.execute(cid, command, principal_id="party")
    final = await restarted_play.store.read(cid)
    continued = await state(restarted_play, cid)
    assert continued.resources.model_copy(update={"revision": native.revision}) == native.resources
    assert next(e for e in continued.resources.events if e.id == ledger_event.id) == ledger_event
    assert read(continued.resources, "continued-battle") == read(
        native.resources, "continued-battle"
    )
    assert continued.actors == native.actors and continued.approvals == native.approvals
    assert continued.recovery == native.recovery and continued.advancement == native.advancement
    assert continued.objectives.outcome == "ongoing"
    assert (await restarted_play.store.history(cid))[: len(history)] == history
    assert (await restarted_play.store.stream(cid))[: len(events)] == events
    assert (
        SetupService.load(final)
        .adventures[0]
        .state.resources.model_copy(update={"revision": native.revision})
        == native.resources
    )
    assert await restarted_play.store.replay(cid) == final
    for checkpoint, _ in await restarted_play.store.stream_states(cid):
        verify(checkpoint)

    def refuse_fresh() -> Never:
        raise AssertionError("Exact continuation retry must not draw new entropy or time")

    restarted_play.seeds = refuse_fresh
    restarted_play.instants = refuse_fresh
    assert await restarted.execute(cid, command, principal_id="party")
    assert await restarted_play.store.read(cid) == final
    assert await restarted_play.store.replay(cid, before["revision"]) == before
