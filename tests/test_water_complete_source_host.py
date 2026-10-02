"""B253 full-source purification needs no unsupported parcel-order assumption."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_play
from test_actions import campaign
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.spell_state import latest as spells
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


async def setup(
    path: Path,
    backend: str,
    *,
    dirty_receiver: bool = False,
    seeded: bool = False,
    two_casters: bool = False,
) -> tuple[str, PlayService, Campaign]:
    cid, play = await prepare(path, backend)
    if two_casters:
        original = play._load(await play.store.read(cid))
        initial = campaign(play.engine)
        await seed_play(
            play,
            initial,
            original.world,
            ResourceState(
                owners=tuple(Owner(actor_id=actor, capacity=100) for actor in ("a", "b")),
                pools=tuple(
                    Pool(
                        id="hp:" + actor,
                        current=10,
                        maximum=10,
                        injury=InjuryStatus(profile_id=PROFILE),
                    )
                    for actor in ("a", "b")
                ),
            ),
            tuple(
                ActorSetup(
                    actor_id=actor,
                    proposal=original.actors[0].proposal,
                    aware_of=("chest", "hidden"),
                )
                for actor in ("a", "b")
            ),
            members=(
                CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
                CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
                CampaignMember(principal_id="gm", role="gm"),
            ),
        )
        cid = initial["id"]
    if seeded:
        play.rng = secrets
        play.seeds = lambda: f"{1:064x}"
    initial = await play.store.read(cid)
    service = WaterService(play)
    for name, gallons, pure, capacity in (
        ("chest", int(dirty_receiver), 0, 5),
        ("hidden", 3, 2, 10),
    ):
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
                    pure_gallons=pure,
                    capacity_gallons=capacity,
                    nature="container" if name == "chest" else "heterogeneous source",
                ),
            ),
            principal_id="gm",
        )
    plan = WaterPlan(
        spell_id="purify-water",
        target_id="chest",
        source_id="hidden",
        gallons=3,
        flowing_through_ring=True,
        purify_entire_source=True,
        allow_receiver_mixing=dirty_receiver,
    )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(id="whole-source", actor_id="a", location_id="dock", plan=plan),
        ),
        principal_id="gm",
    )
    return cid, play, initial


async def start(play: PlayService, cid: str) -> RuntimeSpellCommand:
    command = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="purify-water",
        cast_id="whole",
        channel_id="whole-source",
    )
    await WaterService(play).execute(cid, command, principal_id="alice")
    effect = spells(play._load(await play.store.read(cid)).resources)["whole"]
    assert effect.cost == 3 and effect.ready_at - effect.started_at == 15
    return command


async def concentrate(
    play: PlayService, cid: str, command: RuntimeSpellCommand, ticks: int = 15
) -> None:
    for tick in range(1, ticks + 1):
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
        if tick < 15:
            await WaterService(play).execute(
                cid,
                command.model_copy(
                    update={
                        "id": "concentrate:" + str(tick),
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dirty_receiver", [False, True])
@pytest.mark.parametrize("success", [True, False])
async def test_complete_source_real_material_failure_cost_restart_retry(
    tmp_path: Path, backend: str, dirty_receiver: bool, success: bool
) -> None:
    cid, play, _ = await setup(tmp_path, backend, dirty_receiver=dirty_receiver)
    command = await start(play, cid)
    await concentrate(play, cid, command)
    play.rng = RecordedDice((3, 3, 3) if success else (5, 5, 5))
    complete = command.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await WaterService(play).execute(cid, complete, principal_id="alice")
    assert isinstance(result, SpellResult)
    saved = await play.store.read(cid)
    resources = play._load(saved).resources
    bodies = latest(resources)
    assert (bodies["hidden"].gallons, bodies["hidden"].pure_gallons) == (
        (0, 0) if success else (3, 2)
    )
    assert bodies["chest"].gallons == int(dirty_receiver) + (3 if success else 0)
    assert bodies["chest"].pure_gallons == (3 if success and not dirty_receiver else 0)
    assert result.energy_spent == (3 if success else 1)
    assert next(p.current for p in resources.pools if p.id == "fp:a") == (7 if success else 9)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await WaterService(restarted).execute(cid, complete, principal_id="alice") == result
    assert await play.store.read(cid) == saved
    with pytest.raises(ConflictError):
        await WaterService(restarted).execute(
            cid, complete.model_copy(update={"id": "stale"}), principal_id="alice"
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dirty_receiver", [False, True])
async def test_full_heterogeneous_flow_reexecutes_every_seeded_command(
    tmp_path: Path, backend: str, dirty_receiver: bool
) -> None:
    cid, play, initial = await setup(tmp_path, backend, dirty_receiver=dirty_receiver, seeded=True)
    command = await start(play, cid)
    await concentrate(play, cid, command)
    result = await WaterService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    assert result.outcome == "active"
    final = await play.store.read(cid)
    bodies = latest(play._load(final).resources)
    assert (bodies["hidden"].gallons, bodies["hidden"].pure_gallons) == (0, 0)
    assert (bodies["chest"].gallons, bodies["chest"].pure_gallons) == (
        (4, 0) if dirty_receiver else (3, 3)
    )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "full-source-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_second_cast_changes_source_before_completion_without_partial_drain(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend, two_casters=True)
    command = await start(play, cid)
    await concentrate(play, cid, command, 14)
    service = WaterService(play)
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="other-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="add-water",
                actor_id="b",
                location_id="dock",
                visible=False,
                plan=WaterPlan(
                    spell_id="create-water",
                    target_id="hidden",
                    gallons=1,
                    allow_receiver_mixing=True,
                ),
            ),
        ),
        principal_id="gm",
    )
    other = RuntimeSpellCommand(
        id="other-start",
        actor_id="b",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="add",
        channel_id="add-water",
    )
    await service.execute(cid, other, principal_id="bob")
    await play.execute(
        cid,
        Wait(id="other-tick", actor_id="b", ticks=1, expected_revision=await revision(play, cid)),
        principal_id="b",
    )
    play.rng = RecordedDice((3, 3, 3))
    await service.execute(
        cid,
        other.model_copy(
            update={
                "id": "other-complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="bob",
    )
    before = await play.store.read(cid)
    assert latest(play._load(before).resources)["hidden"].gallons == 4
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="current source"):
        await service.execute(
            cid,
            command.model_copy(
                update={
                    "id": "complete",
                    "kind": "complete",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    assert latest(play._load(before).resources)["chest"].gallons == 0
    assert next(p.current for p in play._load(before).resources.pools if p.id == "fp:a") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_partial_heterogeneous_source_still_requires_composition_and_rolls_back(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await setup(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="current source"):
        await WaterService(play).execute(
            cid,
            DeclareWaterChannel(
                id="partial-channel",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=WaterChannel(
                    id="partial",
                    actor_id="a",
                    location_id="dock",
                    plan=WaterPlan(
                        spell_id="purify-water",
                        target_id="chest",
                        source_id="hidden",
                        gallons=2,
                        flowing_through_ring=True,
                        purify_entire_source=True,
                    ),
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
