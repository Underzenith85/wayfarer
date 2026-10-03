"""Real movement and world-ground consumers retain the created gallon."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_runtime, played
from support.water_inventory import begin, complete, declare, fixture, seeded
from test_gurps_maneuvers import turn
from test_lock_spell_persistence import revision

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.water_inventory import materials
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.size_forms import SizeFormService
from wayfarer.persistence.replay import verify_commands
from wayfarer.transport.v1.projection import Projector


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_original_seed_drop_retrieve_mass_privacy_and_real_move(
    tmp_path: Path, backend: str
) -> None:
    cid, play, genesis = await fixture(tmp_path, backend)
    seeded(play)
    dry = play._load(genesis)
    assert play.engine.resources.carried_weight(dry.resources, "a") == 19250
    assert movement(play.rules_context, dry, "a") == 5
    await declare(play, cid)
    start = await begin(play, cid)
    _, outcome = await complete(play, cid, start, seeded=True)
    assert outcome.outcome == "active"
    state = play._load(await play.store.read(cid))
    assert play.engine.resources.carried_weight(state.resources, "a") == 27250
    assert movement(play.rules_context, state, "a") == 4
    owner_view = Projector(build_runtime(play), "test-water-privacy").make(
        await play.store.read(cid), "alice", "2026-10-03T00:00:00Z"
    )
    assert owner_view.inventories["a"]["total_weight_grams"] == 27250
    water = materials(state.resources)
    steps: tuple[tuple[Literal["drop", "retrieve"], int], ...] = (
        ("drop", 19000),
        ("retrieve", 27250),
    )
    for kind, weight in steps:
        command = WorldGroundCommand(
            kind=kind,
            id="world-water-" + kind,
            actor_id="a",
            item_id="wine-a",
            expected_revision=await revision(play, cid),
        )
        await SizeFormService(play).retrieve(cid, command, principal_id="alice")
        saved = await play.store.read(cid)
        await SizeFormService(play).retrieve(cid, command, principal_id="alice")
        assert await play.store.read(cid) == saved
        state = play._load(saved)
        assert play.engine.resources.carried_weight(state.resources, "a") == weight
        assert materials(state.resources) == water
    for principal in ("alice", "bob", "watcher"):
        projection = str(await build_runtime(play).read(cid, principal_id=principal))
        assert "water-inventory:" not in projection and "physically_touching" not in projection
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="loaded-encounter",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=9, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await turn(cid, play, "a", "move", destination={"x": 5, "y": 0})
    assert await play.store.read(cid) == saved
    await turn(cid, play, "a", "move", destination={"x": 4, "y": 0})
    committed = await play.store.read(cid)
    state = play._load(committed)
    assert state.encounters[0].participants[0].position == GridPoint(x=4, y=0)
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(genesis).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert document(replayed) == document(committed)
