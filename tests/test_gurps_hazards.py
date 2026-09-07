"""Independent numeric fixtures: Campaigns fourth printing B349-354/B430-439."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from test_medical_service import setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec, RecoveryRestriction
from wayfarer.rules.location_types import LastingInjury
from wayfarer.rules.physical import (
    climbing,
    falling_damage,
    hiking_miles,
    jump_distance,
    lift_limit,
)
from wayfarer.simulation.actions import Wait
from wayfarer.simulation.hazards import HazardCommand, apply_hazard
from wayfarer.simulation.medical import BeginRecovery, FinishRecovery
from wayfarer.simulation.resources import Advance


def test_independent_physical_numeric_fixtures() -> None:
    assert climbing("wall", 20) == (-3, 300)
    assert climbing("wall", 20, combat=True) == (-3, 100)
    assert jump_distance(6, kind="high") == Decimal(26) / 36
    assert jump_distance(6, kind="broad", run_yards=100) == 6
    assert jump_distance(6, kind="broad", prepared=False) == Decimal("1.5")
    assert lift_limit(10, "two-hand") == (Decimal(160), 4)
    assert lift_limit(10, "two-hand", margin=5) == (Decimal(200), 4)
    assert hiking_miles(5, success=True, terrain="bad") == 30
    assert falling_damage(10, Decimal(5), hard=True) == (2, 0)
    assert falling_damage(10, Decimal(5), hard=False) == (1, 0)
    assert falling_damage(10, Decimal(5), controlled=True) == (0, 0)


async def test_hazard_deadline_cas_restart_and_shared_clock(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    scene = next(e.location_id for e in state.world.entities if e.id == "a")
    assert scene is not None
    spec = HazardSpec(
        id="cold-room",
        scene_id=scene,
        kind="cold",
        delay=1800,
        interval=1800,
        cycles=3,
        reference="B430",
    )
    service = HazardService(play, lambda *_: HazardContext(spec))
    enter = HazardCommand(
        id="enter", actor_id="a", expected_revision=0, kind="enter", hazard_id=spec.id
    )
    result = await service.execute(cid, enter, authenticated_actor_id="a")
    assert result.due == 1800
    with pytest.raises(ConflictError):
        await play.execute(
            cid,
            Wait(id="skip", actor_id="a", expected_revision=1, ticks=1801),
            authenticated_actor_id="a",
        )
    assert (await play.store.read(cid))["revision"] == 1
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=1800),
        authenticated_actor_id="a",
    )
    play.rng = RecordedDice([5, 5, 5])
    resolve = HazardCommand(
        id="resolve", actor_id="a", expected_revision=2, kind="resolve", hazard_id=spec.id
    )
    results = await asyncio.gather(
        *(service.execute(cid, resolve, authenticated_actor_id="a") for _ in range(3))
    )
    assert all(r == results[0] for r in results)
    assert results[0].fp_lost == 1 and results[0].due == 3600
    restart = HazardService(
        PlayService(play.store, play.engine, rng=RecordedDice([])),
        lambda *_: (_ for _ in ()).throw(AssertionError("replay must not resolve environment")),
    )
    assert await restart.execute(cid, resolve, authenticated_actor_id="a") == results[0]
    after = play._load(await play.store.read(cid))
    assert after.resources.illnesses[0].fp_debt == 1
    assert after.resources.game_time == 1800
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_physical_authority_time_and_durable_result(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    scene = next(e.location_id for e in state.world.entities if e.id == "a")
    assert scene is not None
    route = PhysicalRoute(id="stone", scene_id=scene, pounds=Decimal(150))
    service = PhysicalService(play, lambda *_: route)
    command = PhysicalCommand(
        id="lift", actor_id="a", expected_revision=0, kind="lift", route_id="stone"
    )
    with pytest.raises(ValidationError):
        await service.execute(cid, command, authenticated_actor_id="other")
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert result.succeeded and result.capacity == "160" and result.seconds == 4
    assert play._load(await play.store.read(cid)).resources.game_time == 4
    service.resolver = lambda *_: replace(route, pounds=Decimal(1000))
    assert await service.execute(cid, command, authenticated_actor_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_physical_attempt_cannot_cross_due_exposure(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    scene = next(e.location_id for e in state.world.entities if e.id == "a")
    assert scene is not None
    spec = HazardSpec(
        id="fire",
        scene_id=scene,
        kind="fire",
        delay=2,
        interval=1,
        cycles=10,
        damage_dice=1,
        damage_add=-1,
        resistible=False,
        reference="B433",
    )
    await HazardService(play, lambda *_: HazardContext(spec)).execute(
        cid,
        HazardCommand(
            id="enter", actor_id="a", expected_revision=0, kind="enter", hazard_id="fire"
        ),
        authenticated_actor_id="a",
    )
    service = PhysicalService(play, lambda *_: PhysicalRoute(id="stone", scene_id=scene))
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            PhysicalCommand(
                id="lift", actor_id="a", expected_revision=1, kind="lift", route_id="stone"
            ),
            authenticated_actor_id="a",
        )
    assert (await play.store.read(cid))["revision"] == 1


@pytest.mark.parametrize(
    ("kind", "dice", "hp", "fp"),
    [
        ("fire", [3], 2, 0),
        ("poison", [5, 5, 5], 2, 0),
        ("disease", [5, 5, 5], 2, 0),
        ("heat", [6, 6, 6, 4], 0, 4),
    ],
)
async def test_source_hazard_damage_fixtures(
    tmp_path: Path, kind: str, dice: list[int], hp: int, fp: int
) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    spec = HazardSpec.model_validate(
        {
            "id": "test",
            "scene_id": "square",
            "kind": kind,
            "interval": 1800 if kind == "heat" else 10,
            "cycles": 2,
            "damage_dice": 1 if kind == "fire" else 0,
            "damage_add": -1 if kind == "fire" else 1 if kind == "heat" else 2,
            "resistible": kind != "fire",
            "reference": "B433-439",
        }
    )
    schedule = HazardSchedule(
        id="exposure",
        actor_id="a",
        spec=spec,
        started=0,
        due=0,
        remaining=2,
        ht=10,
        will=10,
        swimming=6,
    )
    resources = state.resources.model_copy(update={"hazards": (schedule,)})
    after, result = apply_hazard(
        resources,
        HazardCommand(
            id="resolve", actor_id="a", expected_revision=0, kind="resolve", hazard_id="test"
        ),
        schedule,
        rng=RecordedDice(dice),
        system=True,
    )
    assert (result.hp_lost, result.fp_lost) == (hp, fp)
    assert next(p.current for p in after.pools if p.id == "hp:a") == 5 - hp
    assert next(p.current for p in after.pools if p.id == "fp:a") == 5 - fp


async def test_no_air_death_deadline_and_drowning_transition(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    spec = HazardSpec(
        id="no-air",
        scene_id="square",
        kind="suffocation",
        delay=1,
        interval=1,
        cycles=240,
        resistible=False,
        reference="B436",
    )
    schedule = HazardSchedule(
        id="exposure",
        actor_id="a",
        spec=spec,
        started=0,
        due=240,
        remaining=1,
        ht=10,
        will=10,
        swimming=6,
        no_air_since=0,
    )
    resources = state.resources.model_copy(update={"game_time": 240, "hazards": (schedule,)})
    after, result = apply_hazard(
        resources,
        HazardCommand(
            id="death", actor_id="a", expected_revision=0, kind="resolve", hazard_id=spec.id
        ),
        schedule,
        rng=RecordedDice([]),
        system=True,
    )
    hp = next(p for p in after.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.dead and not result.active
    drowning = HazardSpec(
        id="water", scene_id="square", kind="drowning", interval=5, cycles=10, reference="B354/B436"
    )
    schedule = HazardSchedule(
        id="water-exposure",
        actor_id="a",
        spec=drowning,
        started=0,
        due=0,
        remaining=10,
        ht=10,
        will=10,
        swimming=12,
        stage="struggling",
    )
    resources = state.resources.model_copy(update={"hazards": (schedule,)})
    after, result = apply_hazard(
        resources,
        HazardCommand(
            id="breathe", actor_id="a", expected_revision=0, kind="resolve", hazard_id=drowning.id
        ),
        schedule,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.fp_lost == 0 and result.due == 60
    assert after.hazards[0].stage == "recovering"


@pytest.mark.parametrize(
    ("kind", "dice", "seconds"),
    [
        ("climb", [3, 3, 3], 3),
        ("jump", [], 3),
        ("hike", [1, 1, 1], 3600),
        ("swim", [3, 3, 3], 1),
        ("fall", [2], 1),
    ],
)
async def test_physical_routes_execute_on_authoritative_clock(
    tmp_path: Path, kind: str, dice: list[int], seconds: int
) -> None:
    cid, play, _ = await setup(tmp_path)
    play.rng = RecordedDice(dice)
    command = PhysicalCommand.model_validate(
        {"id": kind, "actor_id": "a", "expected_revision": 0, "kind": kind, "route_id": "route"}
    )
    route = PhysicalRoute(id="route", scene_id="dock", kind=command.kind, seconds=seconds)
    service = PhysicalService(play, lambda *_: route)
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert result.succeeded and result.seconds == seconds
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == seconds
    assert len([e for e in state.resources.events if e.id == "feat:" + kind]) == 1


async def test_other_subgroup_cannot_advance_past_exposed_actor(tmp_path: Path) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    spec = HazardSpec(
        id="fire", scene_id="dock", kind="fire", delay=2, resistible=False, reference="B433"
    )
    schedule = HazardSchedule(
        id="fire",
        actor_id="a",
        spec=spec,
        started=0,
        due=2,
        remaining=1,
        ht=10,
        will=10,
        swimming=6,
    )
    resources = state.resources.model_copy(update={"hazards": (schedule,)})
    with pytest.raises(ConflictError):
        play.engine.resources.apply(
            resources,
            Advance(id="other-time", actor_id="b", expected_revision=0, to=3),
            system=True,
        )
    after = play.engine.resources.apply(
        resources,
        Advance(id="other-boundary", actor_id="b", expected_revision=0, to=2),
        system=True,
    )
    assert after.game_time == 2 and after.hazards == resources.hazards


async def test_exposure_debt_cannot_be_restored_by_ordinary_rest(tmp_path: Path) -> None:
    cid, play, medical = await setup(tmp_path)
    campaign = await play.store.read(cid)
    before = play._load(campaign)
    # Separate exposure damage from the ordinary four-point deficit.
    restriction = RecoveryRestriction(id="heat", actor_id="a", fp_debt=5, blocks_rest=True)
    state = before.model_copy(
        update={"resources": before.resources.model_copy(update={"illnesses": (restriction,)})}
    )
    campaign["play_json"] = state.model_dump_json()
    other = tmp_path / "restricted.sqlite"
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    play.store = AsyncSQLiteStore(other, 10)
    await play.store.insert(campaign)
    await medical.execute(
        cid,
        BeginRecovery(
            id="rest", actor_id="a", expected_revision=0, kind="rest", target_id="a", seconds=600
        ),
        authenticated_actor_id="a",
    )
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=1, ticks=600),
        authenticated_actor_id="a",
    )
    result = await medical.execute(
        cid,
        FinishRecovery(id="finish", actor_id="a", expected_revision=2, task_id="rest"),
        authenticated_actor_id="a",
    )
    assert result.fp_recovered == 0
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "fp:a"
        )
        == 5
    )


async def test_disabled_leg_cannot_bypass_lasting_injury_through_jump(tmp_path: Path) -> None:
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    cid, play, _ = await setup(tmp_path)
    campaign = await play.store.read(cid)
    state = play._load(campaign)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    wound = LastingInjury(
        id="leg",
        location="left-leg",
        kind="crippled",
        duration="permanent",
        inflicted_at=0,
        injury=6,
    )
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={"anatomy": "human", "lasting_injuries": (wound,)}
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"pools": tuple(hp if p.id == hp.id else p for p in state.resources.pools)}
            )
        }
    )
    campaign["play_json"] = state.model_dump_json()
    play.store = AsyncSQLiteStore(tmp_path / "leg.sqlite", 10)
    await play.store.insert(campaign)
    service = PhysicalService(
        play, lambda *_: PhysicalRoute(id="gap", scene_id="dock", kind="jump")
    )
    with pytest.raises(ValidationError, match="lasting-injury"):
        await service.execute(
            cid,
            PhysicalCommand(
                id="jump", actor_id="a", expected_revision=0, kind="jump", route_id="gap"
            ),
            authenticated_actor_id="a",
        )
    assert (await play.store.read(cid))["revision"] == 0


async def test_exhausted_drowning_checks_will_each_second_without_extra_water_damage(
    tmp_path: Path,
) -> None:
    cid, play, _ = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    spec = HazardSpec(
        id="water", scene_id="dock", kind="drowning", interval=5, cycles=20, reference="B354/B436"
    )
    schedule = HazardSchedule(
        id="water",
        actor_id="a",
        spec=spec,
        started=0,
        due=1,
        next_check_at=5,
        remaining=20,
        ht=10,
        will=10,
        swimming=6,
        stage="struggling",
    )
    resources = state.resources.model_copy(
        update={
            "game_time": 1,
            "hazards": (schedule,),
            "pools": tuple(
                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                for p in state.resources.pools
            ),
        }
    )
    after, result = apply_hazard(
        resources,
        HazardCommand(
            id="will", actor_id="a", expected_revision=0, kind="resolve", hazard_id="water"
        ),
        schedule,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.fp_lost == result.hp_lost == 0
    assert result.consciousness is not None and result.consciousness.outcome.succeeded
    assert result.due == 2 and after.hazards[0].next_check_at == 5


def test_due_hazard_does_not_deadlock_mortality_or_lifesaving_care() -> None:
    from test_advanced_medical import patient

    from wayfarer.simulation.medical import CareContext, apply_recovery

    state = patient(mortal=True)
    spec = HazardSpec(id="fire", scene_id="dock", kind="fire", resistible=False, reference="B433")
    hazard = HazardSchedule(
        id="fire",
        actor_id="a",
        spec=spec,
        started=0,
        due=1800,
        remaining=1,
        ht=10,
        will=10,
        swimming=6,
    )
    fp = next(p for p in state.pools if p.id == "fp:a")
    assert fp.fatigue is not None
    fp = fp.model_copy(
        update={
            "current": -fp.maximum,
            "fatigue": fp.fatigue.model_copy(
                update={"heart_attack": True, "heart_attack_deadline": 2000, "unconscious": True}
            ),
        }
    )
    state = state.model_copy(
        update={
            "game_time": 1800,
            "hazards": (hazard,),
            "pools": tuple(fp if p.id == fp.id else p for p in state.pools),
        }
    )
    context = CareContext("gurps-basic-set-4e-2004", 10, skill=12)
    state, result = apply_recovery(
        state,
        BeginRecovery(
            id="survive", actor_id="a", target_id="a", expected_revision=0, kind="mortal-check"
        ),
        context,
        rng=RecordedDice([3, 3, 4]),
        system=True,
    )
    assert result.check is not None and result.check.outcome.succeeded
    state, result = apply_recovery(
        state,
        BeginRecovery(
            id="rescue", actor_id="b", target_id="a", expected_revision=1, kind="resuscitate"
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.status == "pending" and state.recovery_tasks[0].due == 1860
    assert state.hazards[0].due == state.game_time == 1800
