"""Actual Create Water, measured collection and fresh Purify conserve material."""

from pathlib import Path

import pytest
from support.water_collection import cast, declare, end_old_flow, fixture
from test_lock_spell_persistence import revision

from wayfarer.engine.simulation.magic.water_collection import CollectionReceipt, CollectWater
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_parcels import (
    DeclareWaterParcels,
    ParcelFlow,
    observations,
    pours,
)
from wayfarer.engine.simulation.magic.water_state import latest
from wayfarer.errors import ValidationError
from wayfarer.orchestration.water import WaterService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_create_full_vessel_collection_and_fresh_purify(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await end_old_flow(play, cid, facts)
    created = await cast(
        play, cid, "producer", WaterPlan(spell_id="create-water", target_id="vessel", gallons=1)
    )
    assert created.outcome == "active" and created.energy_spent == 2
    await declare(play, cid)
    before = play._load(await play.store.read(cid))
    immutable = observations(before.resources)
    fp = next(p.current for p in before.resources.pools if p.id == "fp:a")
    result = await WaterService(play).execute(
        cid,
        CollectWater(
            id="collect",
            actor_id="a",
            collection_id="measured-collection",
            expected_revision=before.revision,
        ),
        principal_id="alice",
    )
    assert (
        isinstance(result, CollectionReceipt)
        and result.gallons == 1
        and result.container_gallons == 1
        and result.source_remaining_gallons == 0
        and result.assembly_gallons == 3
        and result.assembly_pure_gallons == 3
        and result.elapsed_seconds == 7
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == before.resources.game_time + 7
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == fp
    assert observations(state.resources) == immutable and len(pours(state.resources)) == 1
    bodies = latest(state.resources)
    assert (
        bodies["vessel"].gallons,
        bodies["hidden"].gallons,
        bodies["hidden"].pure_gallons,
        bodies["chest"].gallons,
    ) == (0, 3, 3, 1)
    material = {(f.subject_id, f.predicate): f.value for f in state.world.facts}
    assert (
        material[("parcel-0", "water_volume_gallons")] == "1"
        and material[("parcel-0", "water_purity")] == "pure"
    )
    with pytest.raises(ValidationError, match="already ended"):
        await cast(
            play,
            cid,
            "old-reuse",
            WaterPlan(
                spell_id="purify-water",
                source_id="hidden",
                target_id="chest",
                gallons=1,
                flowing_through_ring=True,
                parcel_flow_id="old-flow",
            ),
        )
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="new-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="new-flow",
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
    fresh = await cast(
        play,
        cid,
        "fresh-purify",
        WaterPlan(
            spell_id="purify-water",
            source_id="hidden",
            target_id="chest",
            gallons=1,
            flowing_through_ring=True,
            parcel_flow_id="new-flow",
        ),
    )
    assert fresh.outcome == "active" and fresh.checks
    bodies = latest(play._load(await play.store.read(cid)).resources)
    assert (bodies["hidden"].gallons, bodies["hidden"].pure_gallons, bodies["chest"].gallons) == (
        2,
        2,
        2,
    )
    assert sum(b.gallons for b in bodies.values()) == 4
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_create_cannot_authorize_empty_vessel_collection(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await end_old_flow(play, cid, facts)
    failed = await cast(
        play,
        cid,
        "failed-producer",
        WaterPlan(spell_id="create-water", target_id="vessel", gallons=1),
        success=False,
    )
    assert failed.outcome == "failed" and failed.energy_spent == 1
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="homogeneous-water vessel"):
        await declare(play, cid)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_homogeneous_impure_full_collection_then_fresh_purify_changes_material(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend, impure_source=True)
    await end_old_flow(play, cid, facts)
    await declare(play, cid)
    before = play._load(await play.store.read(cid))
    immutable = observations(before.resources)
    result = await WaterService(play).execute(
        cid,
        CollectWater(
            id="impure-collect",
            actor_id="a",
            collection_id="measured-collection",
            expected_revision=before.revision,
        ),
        principal_id="alice",
    )
    assert isinstance(result, CollectionReceipt)
    assert result.assembly_gallons == 3 and result.assembly_pure_gallons == 2
    state = play._load(await play.store.read(cid))
    values = {(f.subject_id, f.predicate): f.value for f in state.world.facts}
    assert (
        values[("vessel", "water_volume_gallons")] == "0"
        and values[("vessel", "water_purity")] == "empty"
    )
    assert (
        values[("parcel-0", "water_volume_gallons")] == "1"
        and values[("parcel-0", "water_purity")] == "impure"
    )
    assert observations(state.resources) == immutable
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="impure-fresh-observation",
            actor_id="gm",
            expected_revision=state.revision,
            flow=ParcelFlow(
                id="impure-fresh-flow",
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
    fp = next(p.current for p in state.resources.pools if p.id == "fp:a")
    result = await cast(
        play,
        cid,
        "impure-fresh-purify",
        WaterPlan(
            spell_id="purify-water",
            source_id="hidden",
            target_id="chest",
            gallons=1,
            flowing_through_ring=True,
            parcel_flow_id="impure-fresh-flow",
        ),
    )
    assert result.outcome == "active" and result.checks and result.energy_spent == 1
    after = play._load(await play.store.read(cid))
    bodies = latest(after.resources)
    assert bodies["vessel"].gallons == 0 and (
        bodies["hidden"].gallons,
        bodies["hidden"].pure_gallons,
    ) == (2, 2)
    assert (bodies["chest"].gallons, bodies["chest"].pure_gallons) == (2, 2)
    assert (
        sum(b.gallons for b in bodies.values()) == 4
        and sum(b.pure_gallons for b in bodies.values()) == 4
    )
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == fp - 1
    assert after.resources.game_time == state.resources.game_time + 5
    assert await play.store.read(cid) == await play.store.replay(cid)
