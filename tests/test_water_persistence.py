"""Private water director declarations use both canonical campaign stores."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_lock_spell_persistence import prepare as lock_prepare
from test_lock_spell_persistence import revision

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.water import package
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel, channels
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService


async def prepare(path: Path, backend: str) -> tuple[str, PlayService]:
    water = package()
    return await lock_prepare(
        path,
        backend,
        extra_definitions=tuple(d for d in water.definitions if d.id.startswith("spell:")),
        extra_sources=water.sources,
        extra_purchases=tuple(
            Purchase(definition_id="spell:" + key, amount=4)
            for key in ("seek-water", "purify-water", "create-water", "destroy-water")
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_declared_material_custody_channel_retry_restart_and_event_replay(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    service = WaterService(play)
    command = DeclareWater(
        id="water",
        actor_id="gm",
        expected_revision=0,
        body=WaterBody(
            object_id="chest",
            location_id="dock",
            gallons=0,
            pure_gallons=0,
            nature="container",
            capacity_gallons=5,
        ),
    )
    receipt = await service.execute(cid, command, principal_id="gm")
    saved = await play.store.read(cid)
    assert await service.execute(cid, command, principal_id="gm") == receipt
    assert await play.store.read(cid) == saved
    assert latest(play._load(saved).resources)["chest"].gallons == 0
    channel = DeclareWaterChannel(
        id="channel",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        channel=WaterChannel(
            id="create",
            actor_id="a",
            location_id="dock",
            plan=WaterPlan(spell_id="create-water", target_id="chest", gallons=2),
        ),
    )
    await service.execute(cid, channel, principal_id="gm")
    saved = await play.store.read(cid)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, store=play.store, rng=RecordedDice(())
    )
    assert channels(restarted._load(saved).resources)[0].plan.gallons == 2
    assert await WaterService(restarted).execute(cid, command, principal_id="gm") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await service.execute(cid, command.model_copy(update={"id": "stale"}), principal_id="gm")
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_player_cannot_author_material_facts(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="fake",
                actor_id="a",
                expected_revision=0,
                body=WaterBody(
                    object_id="chest",
                    location_id="dock",
                    gallons=10,
                    pure_gallons=10,
                    nature="well",
                ),
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
