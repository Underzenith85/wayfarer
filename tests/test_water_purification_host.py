"""Approved B253 flows recontaminate actual opted-in receiving mixtures."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.spell_state import latest as spell_effects
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


async def setup(
    path: Path, backend: str, spell: str, *, seeded: bool = False
) -> tuple[str, PlayService, Campaign]:
    cid, play = await prepare(path, backend)
    service = WaterService(play)
    if seeded:
        play.rng = secrets
        play.seeds = lambda: f"{1:064x}"
    initial = await play.store.read(cid)
    for name, gallons, capacity in (("chest", 1, 5), ("hidden", 10, None)):
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
                    nature="dirty vessel" if capacity else "pond",
                ),
            ),
            principal_id="gm",
        )
    plan = WaterPlan.model_validate(
        dict(
            spell_id=spell,
            target_id="chest",
            gallons=2,
            source_id="hidden" if spell == "purify-water" else None,
            flowing_through_ring=spell == "purify-water",
            allow_receiver_mixing=True,
        )
    )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(id="mixing", actor_id="a", location_id="dock", plan=plan),
        ),
        principal_id="gm",
    )
    return cid, play, initial


async def cast(play: PlayService, cid: str, spell: str) -> tuple[SpellResult, RuntimeSpellCommand]:
    service = WaterService(play)
    start = RuntimeSpellCommand.model_validate(
        dict(
            id="start",
            kind="start",
            actor_id="a",
            expected_revision=await revision(play, cid),
            spell_id=spell,
            cast_id="flow",
            channel_id="mixing",
        )
    )
    await service.execute(cid, start, principal_id="alice")
    seconds = 10 if spell == "purify-water" else 1
    effect = spell_effects(play._load(await play.store.read(cid)).resources)["flow"]
    assert effect.ready_at - effect.started_at == seconds
    assert effect.cost == (2 if spell == "purify-water" else 4)
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
    assert isinstance(result, SpellResult)
    return result, complete


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["create-water", "purify-water"])
@pytest.mark.parametrize("success", [True, False])
async def test_real_cast_mixes_receiver_preserves_cost_failure_and_retry(
    tmp_path: Path, backend: str, spell: str, success: bool
) -> None:
    cid, play, _ = await setup(tmp_path, backend, spell)
    before = play._load(await play.store.read(cid))
    fp = next(p.current for p in before.resources.pools if p.id == "fp:a")
    play.rng = RecordedDice((3, 3, 3) if success else (5, 5, 5))
    result, complete = await cast(play, cid, spell)
    saved = await play.store.read(cid)
    final = play._load(saved)
    assert latest(final.resources)["chest"].gallons == (3 if success else 1)
    assert latest(final.resources)["chest"].pure_gallons == 0
    assert latest(final.resources)["hidden"].gallons == (
        8 if success and spell == "purify-water" else 10
    )
    expected_cost = (2 if spell == "purify-water" else 4) if success else 1
    assert result.energy_spent == expected_cost
    assert next(p.current for p in final.resources.pools if p.id == "fp:a") == fp - expected_cost
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await WaterService(restarted).execute(cid, complete, principal_id="alice") == result
    assert await play.store.read(cid) == saved
    with pytest.raises(ConflictError):
        await WaterService(restarted).execute(
            cid, complete.model_copy(update={"id": "stale"}), principal_id="alice"
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["create-water", "purify-water"])
async def test_mixed_receiver_seeded_commands_reexecute_actual_material(
    tmp_path: Path, backend: str, spell: str
) -> None:
    cid, play, initial = await setup(tmp_path, backend, spell, seeded=True)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    result, _ = await cast(play, cid, spell)
    assert result.outcome == "active"
    final = await play.store.read(cid)
    assert (
        latest(play._load(final).resources)["chest"].gallons,
        latest(play._load(final).resources)["chest"].pure_gallons,
    ) == (3, 0)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "mixture-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_player_cannot_author_or_replace_receiving_material(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend, "purify-water")
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="clean-lie",
                actor_id="a",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id="chest",
                    location_id="dock",
                    gallons=1,
                    pure_gallons=1,
                    capacity_gallons=5,
                    nature="clean lie",
                ),
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == saved
