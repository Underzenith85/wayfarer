"""Independent B404/B436 expectations; supplied Campaigns fourth-printing PDF."""

from pathlib import Path

import pytest
from test_tactical import setup as tactical_setup
from test_unarmed import action, defend, state_of, wait
from test_unarmed_integrations import checkpoint

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ResolveChokeEffects, TakeUnarmedTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.unarmed import fighter
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.combat import CombatEngine


async def setup(tmp_path: Path, holder: str = "a") -> tuple[str, PlayService]:
    cid, play = await tactical_setup(tmp_path, unarmed=True)
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    target = "b" if holder == "a" else "a"
    defender = fighter(encounter, target).model_copy(
        update={"hex_facing": 0 if holder == "a" else 3}
    )
    await checkpoint(
        cid,
        play,
        state.model_copy(
            update={
                "encounters": (CombatEngine._replace(encounter, defender),),
            }
        ),
    )
    if holder == "b":
        await wait(cid, play, "a")
    return cid, play


async def hold(cid: str, play: PlayService, holder: str = "a", skill: str = "skill:judo") -> str:
    state = await state_of(cid, play)
    target = "b" if holder == "a" else "a"
    command = TakeUnarmedTurn.model_validate(
        {
            "id": "choke-hold",
            "actor_id": holder,
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": "grapple",
            "skill": skill,
            "target_id": target,
            "location": "neck",
            "hands": ("left-hand", "right-hand"),
            "enter_close_combat": True,
            "choke_hold": True,
        }
    )
    play.rng = RecordedDice(())
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=holder)
    assert result.available == ("none",)  # Rear hex: no active defense under this profile.
    pending = await state_of(cid, play)
    assert pending.encounters[0].pending_unarmed is not None
    assert pending.encounters[0].pending_unarmed.choke_hold
    assert not pending.encounters[0].grips
    restarted = (
        PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
        if isinstance(play.store, AsyncSQLiteStore)
        else play
    )
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=holder)
        == result
    )
    play.rng = RecordedDice((2, 2, 2))
    await defend(cid, play)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 8
    assert trace.injury == 0 and trace.basic_damage == 0
    assert all(
        p.current == 10 for p in state.resources.pools if p.id in ("hp:a", "hp:b", "fp:a", "fp:b")
    )
    assert fighter(state.encounters[0], target).grappled
    assert play.rng.exhausted()
    grip = state.encounters[0].grips[0]
    assert grip.choke_hold and grip.hazard_id is not None
    return grip.id


async def settle(cid: str, play: PlayService, grip: str, target: str) -> None:
    state = await state_of(cid, play)
    command = ResolveChokeEffects(
        id=f"settle-{state.revision}",
        actor_id=target,
        expected_revision=state.revision,
        encounter_id="fight",
        grip_id=grip,
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=target)
    after = await state_of(cid, play)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=target)
        == result
    )
    assert await state_of(cid, restarted) == after


@pytest.mark.parametrize("holder", ["a", "b"])
@pytest.mark.parametrize("skill", ["skill:judo", "skill:wrestling"])
async def test_choke_hold_ticks_on_holders_following_turn_and_replays(
    tmp_path: Path, holder: str, skill: str
) -> None:
    cid, play = await setup(tmp_path, holder)
    grip = await hold(cid, play, holder, skill)
    target = "b" if holder == "a" else "a"
    play.rng = RecordedDice(())
    before = await state_of(cid, play)
    with pytest.raises(ValidationError, match="holder"):
        await settle(cid, play, grip, target)
    assert await state_of(cid, play) == before
    if holder == "a":
        await wait(cid, play, "b")
    await wait(cid, play, "c")
    # Round two starts at a. A b-owned hold must not tick yet, despite elapsed time.
    if holder == "b":
        before = await state_of(cid, play)
        assert before.resources.game_time == 1
        with pytest.raises(ValidationError, match="holder"):
            await settle(cid, play, grip, target)
        await wait(cid, play, "a")
    before = await state_of(cid, play)
    assert before.resources.game_time == 1
    with pytest.raises(ConflictError, match="suffocation"):
        await wait(cid, play, holder)
    assert await state_of(cid, play) == before
    await settle(cid, play, grip, target)
    after = await state_of(cid, play)
    assert next(p for p in after.resources.pools if p.id == f"fp:{target}").current == 9
    assert after.encounters[0].current_actor_id == holder
    with pytest.raises(ValidationError, match="holder"):
        await settle(cid, play, grip, target)
    await wait(cid, play, holder)
    assert play.rng.exhausted()


async def test_escape_before_holder_turn_cancels_due_clock_tick(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "b")
    grip = await hold(cid, play, "b")
    await wait(cid, play, "c")
    # At t=1 it is a's escape turn, before b's first loss phase.
    play.rng = RecordedDice((2, 2, 2, 5, 5, 5))
    await action(cid, play, "a", "break_free", grip=grip)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.won and not state.encounters[0].grips
    assert trace.checks[0].effective_target == 11
    assert trace.checks[1].effective_target == 16  # ST11 +5, not +10 for two hands.
    assert not state.resources.hazards[0].active
    assert next(p for p in state.resources.pools if p.id == "fp:a").current == 10
    assert play.rng.exhausted()


async def test_optional_crushing_damage_gets_plus_three_and_keeps_existing_exposure(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    grip = await hold(cid, play)
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    await settle(cid, play, grip, "b")
    play.rng = RecordedDice((3, 4, 4, 3, 3, 4))
    await action(cid, play, "a", "strangle", grip=grip)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 13
    assert trace.basic_damage == 2 and trace.injury == 3
    assert len(state.resources.hazards) == 1
    assert state.resources.hazards[0].cycle == 1
    assert state.resources.hazards[0].combat_turn is not None
    assert state.resources.hazards[0].combat_turn.round == 3
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    "change",
    [
        {"skill": "attribute:dx"},
        {"hands": ("left-hand",)},
        {"location": "torso"},
        {"enter_close_combat": False},
        {"action": "kick"},
    ],
)
async def test_unsupported_choke_intent_rejects_before_dice(
    tmp_path: Path, change: dict[str, object]
) -> None:
    cid, play = await setup(tmp_path)
    before = await state_of(cid, play)
    command = {
        "id": "invalid",
        "kind": "take_unarmed_turn",
        "actor_id": "a",
        "expected_revision": before.revision,
        "encounter_id": "fight",
        "action": "grapple",
        "target_id": "b",
        "location": "neck",
        "skill": "skill:judo",
        "hands": ("left-hand", "right-hand"),
        "enter_close_combat": True,
        "choke_hold": True,
        **change,
    }
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_partial_release_rejected_then_full_release_stops_exposure(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    grip = await hold(cid, play)
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    await settle(cid, play, grip, "b")
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="both hands"):
        await action(cid, play, "a", "release", grip=grip, hands=("left-hand",))
    assert await state_of(cid, play) == before
    await action(cid, play, "a", "release", grip=grip)
    after = await state_of(cid, play)
    assert not after.encounters[0].grips and not after.resources.hazards[0].active
    assert not fighter(after.encounters[0], "b").grappled
    assert after.encounters[0].current_actor_id == "a"
    assert next(p for p in after.resources.pools if p.id == "fp:b").current == 9


async def test_third_party_knockout_releases_hold_and_cancels_next_tick(tmp_path: Path) -> None:
    from dataclasses import replace

    from wayfarer.simulation.hex_geometry import Hex

    cid, play = await setup(tmp_path)
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    c = fighter(encounter, "c").model_copy(update={"position": Hex(q=2, r=0), "hex_facing": 3})
    from wayfarer.models import Campaign, Event

    def place_third(campaign: Campaign) -> Event:
        changed = state.model_copy(
            update={
                "encounters": (CombatEngine._replace(encounter, c),),
                "world": replace(state.world, knowledge=state.world.knowledge + (("c", "seen-a"),)),
            }
        )
        play.engine.validate(changed)
        campaign["play_json"] = changed.model_dump_json()
        return Event(input="fixture", action="combat", outcome="third", roll=None)

    await play.store.commit_turn(cid, "place-third", state.revision, "fixture", place_third)
    await hold(cid, play)
    await wait(cid, play, "b")
    await action(cid, play, "c", "kick", target="a")
    play.rng = RecordedDice((2, 3, 3, 6, 6, 5, 5, 6))
    await defend(cid, play)
    state = await state_of(cid, play)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.unconscious
    assert not state.encounters[0].grips
    assert not state.resources.hazards[0].active
    assert next(p for p in state.resources.pools if p.id == "fp:b").current == 10
    assert not fighter(state.encounters[0], "b").grappled
    assert play.rng.exhausted()


async def test_ending_combat_keeps_no_air_exposure_on_shared_clock(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import EndEncounter
    from wayfarer.simulation.hazards import HazardCommand, apply_hazard
    from wayfarer.simulation.resources import Advance

    cid, play = await setup(tmp_path)
    await hold(cid, play)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end-hold",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="scene-ended",
        ),
        authenticated_actor_id="gm",
    )
    state = await state_of(cid, play)
    hazard = state.resources.hazards[0]
    assert hazard.active and hazard.combat_turn is None
    assert hazard.no_air_since == 0 and hazard.due == 1
    resources = play.engine.resources.apply(
        state.resources,
        Advance(
            id="next-second",
            actor_id="b",
            expected_revision=state.resources.revision,
            to=1,
        ),
        system=True,
        rng=play.rng,
    )
    resources, result = apply_hazard(
        resources,
        HazardCommand(
            id="ordinary-exposure",
            actor_id="b",
            expected_revision=resources.revision,
            kind="resolve",
            hazard_id=hazard.spec.id,
        ),
        hazard,
        rng=play.rng,
        system=True,
    )
    assert result.fp_lost == 1
    assert next(p for p in resources.pools if p.id == "fp:b").current == 9


async def test_v2_offers_authorized_hold_and_due_settlement_without_changing_v1(
    tmp_path: Path,
) -> None:
    import aiohttp
    from aiohttp import web

    from wayfarer.orchestration.access import CampaignAccess
    from wayfarer.transport.campaign_api import create_campaign_app

    cid, play = await setup(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play), {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
    )
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    root = f"http://127.0.0.1:{runner.addresses[0][1]}"
    v2 = f"{root}/api/tactical/v2/campaigns/{cid}"
    v1 = f"{root}/api/tactical/v1/campaigns/{cid}"
    alice = {"Authorization": "Bearer alice-token"}
    bob = {"Authorization": "Bearer bob-token"}
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(v2, params={"actor_id": "a"}, headers=alice) as response:
                assert response.status == 200
                data = await response.json()
            choices = data["close_combat_choices"]
            assert len(choices) == 2
            assert all(c["command"]["target_id"] == "b" for c in choices)
            command = choices[0]["command"]
            async with client.get(v1, params={"actor_id": "a"}, headers=alice) as response:
                assert response.status == 200
                assert "close_combat_choices" not in await response.json()
            async with client.post(
                v1 + "/commands", json={"command": command}, headers=alice
            ) as response:
                assert response.status == 400
            async with client.post(
                v2 + "/commands", json={"command": command}, headers=bob
            ) as response:
                assert response.status == 403
            play.rng = RecordedDice(())
            async with client.post(
                v2 + "/commands", json={"command": command}, headers=alice
            ) as response:
                assert response.status == 200, await response.text()
            play.rng = RecordedDice((2, 2, 2))
            await defend(cid, play)
            await wait(cid, play, "b")
            await wait(cid, play, "c")
            async with client.get(v2, params={"actor_id": "b"}, headers=bob) as response:
                assert response.status == 200
                due = (await response.json())["close_combat_choices"]
            assert len(due) == 1 and due[0]["label"] == "Resolve suffocation"
            body = {"command": due[0]["command"]}
            for _ in range(2):
                async with client.post(v2 + "/commands", json=body, headers=bob) as response:
                    assert response.status == 200, await response.text()
            state = await state_of(cid, play)
            assert next(p for p in state.resources.pools if p.id == "fp:b").current == 9
    finally:
        await runner.cleanup()


async def test_unconscious_victim_can_settle_later_hold_phases(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    grip = await hold(cid, play)
    state = await state_of(cid, play)
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 1}) if p.id == "fp:b" else p
                for p in state.resources.pools
            )
        }
    )
    await checkpoint(cid, play, state.model_copy(update={"resources": resources}))
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    play.rng = RecordedDice((6, 5, 5))
    await settle(cid, play, grip, "b")
    state = await state_of(cid, play)
    fp = next(p for p in state.resources.pools if p.id == "fp:b")
    assert fp.current == 0 and fp.fatigue is not None and fp.fatigue.unconscious
    await wait(cid, play, "a")
    assert (await state_of(cid, play)).encounters[0].current_actor_id == "c"
    await wait(cid, play, "c")
    play.rng = RecordedDice((6, 5, 5))
    await settle(cid, play, grip, "b")
    state = await state_of(cid, play)
    assert next(p for p in state.resources.pools if p.id == "fp:b").current == -1
