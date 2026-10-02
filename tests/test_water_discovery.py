"""B253 findings belong to the caster and admit later source exclusions."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign
from test_lock_spell_persistence import prepare as lock_prepare
from test_lock_spell_persistence import revision

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.water import package
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_discovery import WaterSpellResult, known_sources
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str) -> tuple[str, PlayService]:
    water = package()
    source_id, source = await lock_prepare(
        path / "source",
        backend,
        combat=True,
        extra_definitions=tuple(d for d in water.definitions if d.id.startswith("spell:")),
        extra_sources=water.sources,
        extra_purchases=(Purchase(definition_id="spell:seek-water", amount=4),),
    )
    state = source._load(await source.store.read(source_id))
    play = build_play(path / "actual", source.engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        state.world,
        state.resources,
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=state.actors[0].proposal) for a in state.actors
        ),
        state.members + (CampaignMember(principal_id="watcher", role="spectator"),),
    )
    return initial["id"], play


async def source_and_channel(
    play: PlayService,
    cid: str,
    *,
    identifier: str = "seek",
    actor: str = "a",
    excluded: tuple[str, ...] = (),
) -> None:
    service = WaterService(play)
    if identifier == "seek":
        await service.execute(
            cid,
            DeclareWater(
                id="water-source",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id="hidden",
                    location_id="dock",
                    position=(7, 2),
                    gallons=10,
                    pure_gallons=0,
                    nature="buried pond",
                    significant=True,
                ),
            ),
            principal_id="gm",
        )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="channel:" + identifier,
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id=identifier,
                actor_id=actor,
                location_id="dock",
                plan=WaterPlan(
                    spell_id="seek-water", target_id=actor, excluded_source_ids=excluded
                ),
            ),
        ),
        principal_id="gm",
    )


async def cast(
    play: PlayService, cid: str, identifier: str, *, success: bool = True, seeded: bool = False
) -> tuple[RuntimeSpellCommand, SpellResult]:
    service = WaterService(play)
    start = RuntimeSpellCommand(
        id=identifier + ":start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="seek-water",
        cast_id=identifier,
        channel_id=identifier,
    )
    await service.execute(cid, start, principal_id="alice")
    await play.execute(
        cid,
        Wait(
            id=identifier + ":wait",
            actor_id="a",
            expected_revision=await revision(play, cid),
            ticks=1,
        ),
        principal_id="a",
    )
    if not seeded:
        play.rng = RecordedDice((3, 3, 3) if success else (5, 5, 5))
    complete = start.model_copy(
        update={
            "id": identifier + ":complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await service.execute(cid, complete, principal_id="alice")
    assert isinstance(result, SpellResult)
    return complete, result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_caster_receives_finding_and_remembers_source_privately_after_restart(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await source_and_channel(play, cid)
    complete, result = await cast(play, cid, "seek")
    assert isinstance(result, WaterSpellResult) and result.finding is not None
    assert result.finding.model_dump() == {
        "direction": (7, 2),
        "distance_yards": 8,
        "nature": "buried pond",
    }
    assert (
        "hidden" not in result.model_dump_json()
        and "water-cast-plan:" not in result.model_dump_json()
    )
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert known_sources(state.resources, "a") == {"hidden"}
    assert known_sources(state.resources, "b") == set()
    assert "hidden" not in {e.id for e in state.world.perspective("a").entities}
    runtime = build_runtime(play)
    own = await runtime.read(cid, principal_id="alice")
    assert own["water_findings"] == (
        {
            "actor_id": "a",
            "at": 1,
            "direction": [7, 2],
            "distance_yards": 8,
            "nature": "buried pond",
        },
    )
    for principal in ("bob", "watcher"):
        view = json.dumps(await runtime.read(cid, principal_id=principal))
        events = json.dumps(
            [e.model_dump(mode="json") for e in await runtime.events(cid, principal_id=principal)]
        )
        assert "buried pond" not in view + events and "water_findings" not in view + events
        with pytest.raises((AuthorizationError, ValidationError)):
            await WaterService(play).execute(cid, complete, principal_id=principal)
    assert any(
        "buried pond" in e.model_dump_json()
        for e in await runtime.events(cid, principal_id="alice")
    )
    restarted = build_play(
        tmp_path / "restart", play.engine, backend=backend, store=play.store, rng=RecordedDice(())
    )
    assert await WaterService(restarted).execute(cid, complete, principal_id="alice") == result
    assert await restarted.store.read(cid) == saved
    with pytest.raises(ValidationError, match="Only known sources"):
        await source_and_channel(
            restarted, cid, identifier="other", actor="b", excluded=("hidden",)
        )
    assert await restarted.store.read(cid) == saved
    await source_and_channel(restarted, cid, identifier="excluded", excluded=("hidden",))
    _, second = await cast(restarted, cid, "excluded")
    assert isinstance(second, WaterSpellResult) and second.finding is not None
    assert second.finding.model_dump() == {
        "direction": None,
        "distance_yards": None,
        "nature": None,
    }
    assert await WaterService(restarted).execute(cid, complete, principal_id="alice") == result
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_seek_grants_neither_receipt_finding_nor_exclusion_knowledge(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await source_and_channel(play, cid)
    _, result = await cast(play, cid, "seek", success=False)
    assert result.outcome == "failed" and not isinstance(result, WaterSpellResult)
    saved = await play.store.read(cid)
    assert not known_sources(play._load(saved).resources, "a")
    assert "water_findings" not in await build_runtime(play).read(cid, principal_id="alice")
    with pytest.raises(ValidationError, match="Only known sources"):
        await source_and_channel(play, cid, identifier="excluded", excluded=("hidden",))
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_remembered_source_exclusion_and_private_projection_reexecute_from_seed(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await source_and_channel(play, cid)
    _, first = await cast(play, cid, "seek", seeded=True)
    assert isinstance(first, WaterSpellResult) and first.finding is not None
    assert first.finding.nature == "buried pond"
    await source_and_channel(play, cid, identifier="excluded", excluded=("hidden",))
    complete, second = await cast(play, cid, "excluded", seeded=True)
    assert (
        isinstance(second, WaterSpellResult)
        and second.finding is not None
        and second.finding.nature is None
    )
    final = await play.store.read(cid)
    assert await WaterService(play).execute(cid, complete, principal_id="alice") == second
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
    assert known_sources(play._load(replayed).resources, "a") == {"hidden"}
