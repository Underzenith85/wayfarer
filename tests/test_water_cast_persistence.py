"""Actual approved B253 water commands: material, energy, failure and retries."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.spell_state import latest as spell_effects
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import (
    DISCOVERY_PREFIX,
    WaterDiscovery,
    WaterPlan,
)
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.errors import ConflictError
from wayfarer.orchestration.water import WaterService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "spell,cost,seconds",
    [
        ("seek-water", 2, 1),
        ("purify-water", 2, 10),
        ("create-water", 4, 1),
        ("destroy-water", 3, 1),
    ],
)
@pytest.mark.parametrize("success", [True, False])
async def test_actual_cast_material_energy_failure_retry_restart(
    tmp_path: Path, backend: str, spell: str, cost: int, seconds: int, success: bool
) -> None:
    cid, play = await prepare(tmp_path, backend)
    service = WaterService(play)
    for object_id, gallons, capacity in (("chest", 0, 5), ("hidden", 10, None)):
        await service.execute(
            cid,
            DeclareWater(
                id="body:" + object_id,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id=object_id,
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
            target_id="a"
            if spell == "seek-water"
            else "hidden"
            if spell == "destroy-water"
            else "chest",
            gallons=2,
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
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="start",
            spell_id=spell,
            cast_id="water",
            channel_id="water",
        )
    )
    before = play._load(await play.store.read(cid))
    fp_before = next(p.current for p in before.resources.pools if p.id == "fp:a")
    await service.execute(cid, start, principal_id="alice")
    started = play._load(await play.store.read(cid))
    effect = spell_effects(started.resources)["water"]
    assert effect.cost == cost
    assert effect.ready_at - effect.started_at == seconds
    assert latest(started.resources) == latest(before.resources)
    for tick in range(1, seconds + 1):
        await play.execute(
            cid,
            Wait(
                id="wait:" + str(tick),
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        if tick != seconds:
            await service.execute(
                cid,
                start.model_copy(
                    update={
                        "id": "concentrate:" + str(tick),
                        "expected_revision": await revision(play, cid),
                        "kind": "concentrate",
                    }
                ),
                principal_id="alice",
            )
    play.rng = RecordedDice((3, 3, 3) if success else (5, 5, 5))
    complete = start.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await service.execute(cid, complete, principal_id="alice")
    assert isinstance(result, SpellResult)
    saved = await play.store.read(cid)
    final = play._load(saved)
    assert next(p.current for p in final.resources.pools if p.id == "fp:a") == fp_before - (
        cost if success else 1
    )
    bodies = latest(final.resources)
    if not success:
        assert result.outcome == "failed"
        assert bodies == latest(before.resources)
        assert not any(e.id.startswith(DISCOVERY_PREFIX) for e in final.resources.events)
    elif spell == "purify-water":
        assert bodies["hidden"].gallons == 8
        assert bodies["chest"].gallons == bodies["chest"].pure_gallons == 2
    elif spell == "create-water":
        assert bodies["hidden"].gallons == 10
        assert bodies["chest"].gallons == bodies["chest"].pure_gallons == 2
    elif spell == "destroy-water":
        assert bodies["hidden"].gallons == bodies["hidden"].pure_gallons == 0
    else:
        discovery = WaterDiscovery.model_validate_json(
            next(e.kind for e in final.resources.events if e.id.startswith(DISCOVERY_PREFIX))
        )
        assert (discovery.actor_id, discovery.source_id, discovery.nature) == (
            "a",
            "hidden",
            "pond",
        )
        assert bodies == latest(before.resources)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await WaterService(restarted).execute(cid, complete, principal_id="alice") == result
    assert await play.store.read(cid) == saved
    assert saved == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await WaterService(restarted).execute(
            cid, complete.model_copy(update={"id": "stale"}), principal_id="alice"
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("substitution", ["channel", "gallons", "actor"])
async def test_cast_commitment_substitution_rolls_back_without_dice(
    tmp_path: Path, backend: str, substitution: str
) -> None:
    from wayfarer.errors import AuthorizationError, ValidationError

    cid, play = await prepare(tmp_path, backend)
    service = WaterService(play)
    await service.execute(
        cid,
        DeclareWater(
            id="body",
            actor_id="gm",
            expected_revision=0,
            body=WaterBody(
                object_id="chest",
                location_id="dock",
                gallons=0,
                pure_gallons=0,
                capacity_gallons=10,
                nature="container",
            ),
        ),
        principal_id="gm",
    )
    original = WaterPlan(spell_id="create-water", target_id="chest", gallons=2)
    for channel_id, gallons in (("original", 2), ("same-plan", 2), ("more-water", 3)):
        await service.execute(
            cid,
            DeclareWaterChannel(
                id="declare:" + channel_id,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=WaterChannel(
                    id=channel_id,
                    actor_id="a",
                    location_id="dock",
                    plan=original.model_copy(update={"gallons": gallons}),
                ),
            ),
            principal_id="gm",
        )
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="water",
        channel_id="original",
    )
    await service.execute(cid, start, principal_id="alice")
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    before = await play.store.read(cid)
    substituted = start.model_copy(
        update={
            "id": "changed",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
            "channel_id": "same-plan"
            if substitution == "channel"
            else "more-water"
            if substitution == "gallons"
            else "original",
            "actor_id": "b" if substitution == "actor" else "a",
        }
    )
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, AuthorizationError, ValidationError)):
        await service.execute(cid, substituted, principal_id="alice")
    assert await play.store.read(cid) == before
    assert latest(play._load(before).resources)["chest"].gallons == 0
    play.rng = RecordedDice((3, 3, 3))
    proper = start.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    await service.execute(cid, proper, principal_id="alice")
    assert latest(play._load(await play.store.read(cid)).resources)["chest"].gallons == 2
