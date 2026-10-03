"""The whole producer/collection/fresh-purification campaign reexecutes."""

from pathlib import Path

import pytest
from support.runtime import played
from support.water_collection import cast, declare, end_old_flow, fixture, seed
from test_lock_spell_persistence import revision

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.water_collection import CollectWater
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_parcels import DeclareWaterParcels, ParcelFlow
from wayfarer.engine.simulation.magic.water_state import latest
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_create_collection_fresh_purify_full_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, genesis = await fixture(tmp_path, backend)
    seed(play)
    await end_old_flow(play, cid, facts, seeded=True)
    made = await cast(
        play,
        cid,
        "producer",
        WaterPlan(spell_id="create-water", target_id="vessel", gallons=1),
        seeded=True,
    )
    assert made.outcome == "active"
    await declare(play, cid)
    result = await WaterService(play).execute(
        cid,
        CollectWater(
            id="collect",
            actor_id="a",
            collection_id="measured-collection",
            expected_revision=await revision(play, cid),
        ),
        principal_id="alice",
    )
    assert result.outcome == "collected"
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="fresh-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="fresh-flow",
                caster_id="a",
                source_id="hidden",
                target_id="chest",
                ring_id="ring",
                container_ids=("parcel-0", "parcel-1", "parcel-2"),
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )
    await cast(
        play,
        cid,
        "fresh-purify",
        WaterPlan(
            spell_id="purify-water",
            source_id="hidden",
            target_id="chest",
            gallons=1,
            flowing_through_ring=True,
            parcel_flow_id="fresh-flow",
        ),
        seeded=True,
    )
    committed = await play.store.read(cid)
    bodies = latest(play._load(committed).resources)
    assert (bodies["vessel"].gallons, bodies["hidden"].gallons, bodies["chest"].gallons) == (
        0,
        2,
        2,
    )
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(genesis).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert document(replayed) == document(committed)
