"""B354–355 swimming consumes the B481 nightly rest interval and real health."""

import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

import pytest
from support.runtime import build_play, played
from test_enchanting_nighttime import advance_work, light
from test_enchanting_physical_nighttime import configured as physical_configured
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.campaign.default_knowledge import known_skills, observations
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.hazards import HazardService
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.swim_hazard_resume import capture_swim_resume
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands


async def configured(
    path: Path, backend: str = "sqlite"
) -> tuple[Campaign, PlayService, PlayState]:
    initial, play, genesis = await physical_configured(path, backend, society_knows_swimming=True)
    state = play._load(await play.store.read(initial["id"]))
    assert not observations(genesis.resources)
    assert known_skills(state, "a") == frozenset({"skill:swimming"})
    assert not known_skills(state, "b")
    return initial, play, genesis


def route(*, seconds: int = 60, distance: int = 60) -> PhysicalRoute:
    return PhysicalRoute(
        id="night-swim", scene_id="forge", kind="swim", seconds=seconds, distance=Decimal(distance)
    )


async def command(play: PlayService, cid: str, identifier: str = "swim") -> PhysicalCommand:
    return PhysicalCommand(
        id=identifier,
        actor_id="a",
        expected_revision=play._load(await play.store.read(cid)).revision,
        kind="swim",
        route_id="night-swim",
    )


async def wait(play: PlayService, cid: str, identifier: str, seconds: int) -> None:
    await play.execute(
        cid,
        Wait(
            id=identifier,
            actor_id="a",
            expected_revision=play._load(await play.store.read(cid)).revision,
            ticks=seconds,
        ),
        principal_id="a",
    )


def unavailable(*_: object) -> NoReturn:
    raise AssertionError("Recorded native swimming must not consult a new resolver")


async def resolve(play: PlayService, cid: str, identifier: str) -> HazardCommand:
    state = play._load(await play.store.read(cid))
    return HazardCommand(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        kind="resolve",
        hazard_id=state.resources.hazards[0].spec.id,
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("at", [28800, 86340])
async def test_full_minute_swimming_records_sixty_yards_and_fatigue(
    tmp_path: Path, backend: str, at: int
) -> None:
    initial, play, _ = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, at)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 4, 4, 3))
    swim = await command(play, cid)
    first = await PhysicalService(play, lambda *_: route()).execute(cid, swim, principal_id="a")
    assert first.succeeded and first.completed and first.seconds == 60
    assert first.progress == "60" and first.capacity == "60" and first.elapsed == 60
    assert [(c.effective_target, c.total) for c in first.checks] == [(9, 9), (10, 11)]
    assert first.fp_lost == 1 and first.injury == 0
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == at + 60
    assert state.resources.enchantment_projects == before.resources.enchantment_projects
    assert not state.resources.hazards
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await PhysicalService(restarted, unavailable).execute(cid, swim, principal_id="a") == first
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_forty_second_route_uses_only_twenty_seconds_to_fatigue_boundary(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 86340)
    service = PhysicalService(play, lambda *_: route(seconds=40, distance=100))
    play.rng = RecordedDice((3, 3, 3))
    first = await service.execute(cid, await command(play, cid), principal_id="a")
    assert first.seconds == 40 and first.progress == "40" and first.elapsed == 40
    play.rng = RecordedDice((4, 4, 3))
    second = await service.execute(cid, await command(play, cid, "swim-second"), principal_id="a")
    assert second.seconds == 20 and second.progress == "60" and second.elapsed == 60
    assert second.checks[0].effective_target == 10 and second.fp_lost == 1
    assert play._load(await play.store.read(cid)).resources.game_time == 86400


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_swimming_fatigue_and_native_drowning_reexecute_from_genesis(
    tmp_path: Path, backend: str
) -> None:
    initial, play, genesis = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    project = play._load(await play.store.read(cid)).resources.enchantment_projects[0]
    # Seed2 gives a successful initial6 against9, then fatigue13 against10.
    play.seeds = lambda: f"{2:064x}"
    service = PhysicalService(play, lambda *_: route(distance=200))
    swim = await command(play, cid)
    first, duplicate = await asyncio.gather(
        *(service.execute(cid, swim, principal_id="a") for _ in range(2))
    )
    assert first == duplicate and first.succeeded and first.fp_lost == 1
    assert first.seconds == 60 and first.progress == "60"
    # New route ID permits another intentional entry, which seed3 fails at11.
    failed_route = replace(route(), id="failed-entry")
    play.seeds = lambda: f"{3:064x}"
    failure = (await command(play, cid, "failed-swim")).model_copy(
        update={"route_id": failed_route.id}
    )
    failed = await PhysicalService(play, lambda *_: failed_route).execute(
        cid, failure, principal_id="a"
    )
    assert not failed.succeeded and failed.seconds == 1 and failed.fp_lost == 1
    state = play._load(await play.store.read(cid))
    hazard = state.resources.hazards[0]
    assert hazard.started == 28861 and hazard.due == 28866 and hazard.stage == "struggling"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="hazard"):
        await wait(play, cid, "skip-drowning", 6)
    assert before == await play.store.read(cid)
    await wait(play, cid, "first-deadline", 5)
    drowning = await resolve(play, cid, "drowning-failure")
    first_hazard = await HazardService(play, unavailable).execute(cid, drowning, principal_id="a")
    assert first_hazard.check and first_hazard.check.effective_target == 6
    assert (
        first_hazard.check.total == 11 and first_hazard.fp_lost == 1 and first_hazard.due == 28871
    )
    await wait(play, cid, "recover-deadline", 5)
    play.seeds = lambda: f"{2:064x}"
    recovery_command = await resolve(play, cid, "drowning-recovery")
    recovery = await HazardService(play, unavailable).execute(
        cid, recovery_command, principal_id="a"
    )
    assert recovery.check and recovery.check.total == 6 and recovery.fp_lost == 0
    assert recovery.due == 28931
    state = play._load(await play.store.read(cid))
    assert state.resources.hazards[0].stage == "recovering"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert state.resources.enchantment_projects[0] == project
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await HazardService(restarted, unavailable).execute(cid, recovery_command, principal_id="a")
        == recovery
    )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("at", [0, 86341, 86400])
async def test_swimming_entire_planned_minute_must_fit_before_dice(tmp_path: Path, at: int) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    if at:
        await advance_work(play, cid, at)
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ConflictError, match="enchanting work|enchanting work shift"):
        await PhysicalService(play, lambda *_: route()).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert dice.exhausted() and before == await play.store.read(cid)


@pytest.mark.parametrize("change", ["group", "destination", "exit", "duration"])
async def test_night_swimming_does_not_admit_group_or_scene_travel(
    tmp_path: Path, change: str
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    authored = route()
    if change == "group":
        authored = replace(authored, group_hike=True)
    elif change == "destination":
        authored = replace(authored, destination_id="elsewhere")
    elif change == "exit":
        authored = replace(authored, exit_id="out")
    else:
        authored = replace(authored, seconds=61)
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ValidationError):
        await PhysicalService(play, lambda *_: authored).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert dice.exhausted() and before == await play.store.read(cid)


async def test_current_swim_geometry_authority_concentration_and_historical_admission(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    swim = await command(play, cid)
    service = PhysicalService(play, lambda *_: route(seconds=40, distance=100))
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, swim, principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, swim.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    with pytest.raises(ConflictError, match="enchanting work"):
        await submit(play, cid, service.plan(play, swim, principal_id="a"), principal_id="a")
    assert before == await play.store.read(cid)
    play.rng = RecordedDice((3, 3, 3))
    await service.execute(cid, swim, principal_id="a")
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ValidationError, match="geometry changed"):
        await PhysicalService(play, lambda *_: route(seconds=30, distance=100)).execute(
            cid, await command(play, cid, "changed-route"), principal_id="a"
        )
    assert dice.exhausted() and before == await play.store.read(cid)
    await SpellService(play).execute(
        cid, light("pending", play._load(before).revision), principal_id="a"
    )
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="concentrat|cast"):
        await service.execute(cid, await command(play, cid, "pending-swim"), principal_id="a")
    assert before == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_swim_and_drowning_health_rollbacks_are_atomic_and_retry_once(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    swim = await command(play, cid)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((4, 4, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            PhysicalService(failing, lambda *_: route()).plan(
                failing, swim, principal_id="a", route=route()
            ),
            principal_id="a",
        )
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and stream == await play.store.stream(cid)
    play.rng = RecordedDice((4, 4, 3))
    failed = await PhysicalService(play, lambda *_: route()).execute(cid, swim, principal_id="a")
    assert not failed.succeeded and failed.fp_lost == 1
    await wait(play, cid, "drowning-deadline", 5)
    hazard_command = await resolve(play, cid, "recover")
    source = await capture_swim_resume(
        play, cid, hazard_command, play._load(await play.store.read(cid))
    )
    assert source is not None
    before = await play.store.read(cid)
    service = HazardService(play, unavailable)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, hazard_command, principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, hazard_command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert before == await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((4, 4, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            HazardService(failing, unavailable).plan(
                failing, hazard_command, principal_id="a", swim_resume=source
            ),
            principal_id="a",
        )
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and stream == await play.store.stream(cid)
    play.rng = RecordedDice((1, 2, 3))
    first, duplicate = await asyncio.gather(
        *(service.execute(cid, hazard_command, principal_id="a") for _ in range(2))
    )
    assert first == duplicate and first.fp_lost == 0 and first.due == 28866
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await HazardService(restarted, unavailable).execute(cid, hazard_command, principal_id="a")
        == first
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_swimming_distress_and_next_hazard_boundary_refuse_before_entropy(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    service = PhysicalService(play, lambda *_: route(distance=200))
    play.rng = RecordedDice((4, 4, 3))
    await service.execute(cid, await command(play, cid), principal_id="a")
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ValidationError, match="distress"):
        await service.execute(cid, await command(play, cid, "struggling"), principal_id="a")
    assert dice.exhausted() and before == await play.store.read(cid)
    await wait(play, cid, "recovery-check", 5)
    play.rng = RecordedDice((1, 2, 3))
    await HazardService(play, unavailable).execute(
        cid, await resolve(play, cid, "recover"), principal_id="a"
    )
    play.rng = RecordedDice((3, 3, 3))
    next_step = await service.execute(
        cid, await command(play, cid, "continued-swim"), principal_id="a"
    )
    assert next_step.seconds == 59 and next_step.elapsed == 60 and next_step.progress == "59"
    assert next_step.fp_lost == 0
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 28865 and state.resources.hazards[0].due == 28866
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ConflictError, match="hazard"):
        await service.execute(cid, await command(play, cid, "cross-hazard"), principal_id="a")
    assert dice.exhausted() and before == await play.store.read(cid)
    await wait(play, cid, "hazard-boundary", 1)
    play.rng = RecordedDice((1, 2, 3))
    settled = await HazardService(play, unavailable).execute(
        cid, await resolve(play, cid, "steady-swim"), principal_id="a"
    )
    assert settled.due == 29166 and settled.fp_lost == 0
    assert play._load(await play.store.read(cid)).resources.hazards[0].stage == "swimming"


@pytest.mark.parametrize(
    "corruption", ["digest", "producer", "route", "result", "deadline", "cycle", "actor"]
)
async def test_native_drowning_capture_refuses_changed_source_before_dice(
    tmp_path: Path, corruption: str
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    play.rng = RecordedDice((4, 4, 3))
    await PhysicalService(play, lambda *_: route()).execute(
        cid, await command(play, cid), principal_id="a"
    )
    await wait(play, cid, "deadline", 5)
    hazard_command = await resolve(play, cid, "recover")
    source = await capture_swim_resume(
        play, cid, hazard_command, play._load(await play.store.read(cid))
    )
    assert source is not None
    if corruption == "digest":
        source = source.model_copy(update={"producer_input_digest": "0" * 64})
    elif corruption in ("producer", "route"):
        payload = replay_payload(source.producer_input)
        assert isinstance(payload, dict)
        if corruption == "producer":
            assert isinstance(payload["command"], dict)
            payload["command"]["id"] = "another-swimmer"
        else:
            assert isinstance(payload["physical_route"], str)
            authored = json.loads(payload["physical_route"])
            authored["seconds"] = 30
            payload["physical_route"] = json.dumps(authored, sort_keys=True)
        encoded = json.dumps(payload, sort_keys=True)
        source = source.model_copy(
            update={
                "producer_input": encoded,
                "producer_input_digest": payload_digest({"input": encoded}),
            }
        )
    elif corruption == "result":
        source = source.model_copy(
            update={"producer_result": source.producer_result.model_copy(update={"fp_lost": 2})}
        )
    else:
        updates = (
            {"due": source.schedule.due + 1}
            if corruption == "deadline"
            else {"cycle": source.schedule.cycle + 1}
            if corruption == "cycle"
            else {"actor_id": "b"}
        )
        source = source.model_copy(update={"schedule": source.schedule.model_copy(update=updates)})
    before = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError), match="producer|schedule|digest"):
        await submit(
            play,
            cid,
            HazardService(play, unavailable).plan(
                play, hazard_command, principal_id="a", swim_resume=source
            ),
            principal_id="a",
        )
    assert dice.exhausted() and before == await play.store.read(cid)
