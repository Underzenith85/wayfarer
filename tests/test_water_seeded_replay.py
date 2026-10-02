"""Seeded re-execution folds actual B253 effects, not just saved checkpoints."""

import secrets
from pathlib import Path

import pytest
from support.runtime import played
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import (
    DISCOVERY_PREFIX,
    WaterDiscovery,
    WaterPlan,
)
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "spell,seconds",
    [
        ("seek-water", 1),
        ("purify-water", 10),
        ("create-water", 1),
        ("destroy-water", 1),
    ],
)
async def test_all_water_spells_reexecute_from_seed_and_preserve_material(
    tmp_path: Path, backend: str, spell: str, seconds: int
) -> None:
    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    service = WaterService(play)
    for name, gallons, capacity in (("chest", 0, 5), ("hidden", 10, None)):
        await service.execute(
            cid,
            DeclareWater(
                id="body:" + name,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id=name,
                    location_id="dock",
                    gallons=gallons,
                    pure_gallons=0,
                    capacity_gallons=capacity,
                    nature="pond" if gallons else "container",
                    significant=bool(gallons),
                ),
            ),
            principal_id="gm",
        )
    plan = WaterPlan.model_validate(
        dict(
            spell_id=spell,
            gallons=2,
            target_id="a"
            if spell == "seek-water"
            else "hidden"
            if spell == "destroy-water"
            else "chest",
            source_id="hidden" if spell == "purify-water" else None,
            flowing_through_ring=spell == "purify-water",
            destroyed_ids=("hidden",) if spell == "destroy-water" else (),
        )
    )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(id="water", actor_id="a", location_id="dock", plan=plan),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand.model_validate(
        dict(
            id="start",
            kind="start",
            actor_id="a",
            expected_revision=await revision(play, cid),
            spell_id=spell,
            cast_id="water",
            channel_id="water",
        )
    )
    await service.execute(cid, start, principal_id="alice")
    for tick in range(1, seconds + 1):
        await play.execute(
            cid,
            Wait(
                id="tick:" + str(tick),
                actor_id="a",
                ticks=1,
                expected_revision=await revision(play, cid),
            ),
            principal_id="a",
        )
        if tick < seconds:
            await service.execute(
                cid,
                start.model_copy(
                    update={
                        "id": "concentrate:" + str(tick),
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )
    complete = start.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await service.execute(cid, complete, principal_id="alice")
    assert result.outcome == "active"
    committed = await play.store.read(cid)
    bodies = latest(play._load(committed).resources)
    if spell == "create-water":
        assert bodies["chest"].gallons == bodies["chest"].pure_gallons == 2
    elif spell == "purify-water":
        assert bodies["chest"].gallons == bodies["chest"].pure_gallons == 2
        assert bodies["hidden"].gallons == 8
    elif spell == "destroy-water":
        assert bodies["hidden"].gallons == 0
    else:
        discovery = WaterDiscovery.model_validate_json(
            next(
                e.kind
                for e in play._load(committed).resources.events
                if e.id.startswith(DISCOVERY_PREFIX)
            )
        )
        assert (discovery.actor_id, discovery.source_id, discovery.nature) == (
            "a",
            "hidden",
            "pond",
        )
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", ticks=120, expected_revision=await revision(play, cid)),
        principal_id="a",
    )
    final = await play.store.read(cid)
    assert latest(play._load(final).resources) == bodies
    assert await service.execute(cid, complete, principal_id="alice") == result
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "water-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
