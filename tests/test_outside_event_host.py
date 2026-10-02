"""Real outside-event consequences share B66 authority, persistence and time."""

import asyncio
import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_orchestrator, build_play, build_runtime
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_outside_event_damage import flame
from test_secret_task_boundaries import revoke_owner
from test_task_host import fixture
from test_wave9 import FakeProvider

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.environmental_hazards import acid_spec
from wayfarer.engine.rules.types.hazard import HazardProtection, HazardSpec
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.health.hazards import HazardCommand
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.outside_event_records import (
    ChooseOutsideEvent,
    NaturalExposureDeclaration,
    OutsideEventOutcome,
    OutsideEventPending,
    PrepareOutsideEvent,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import RESULT_PREFIX, TaskResult, identity, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands

Choice = Literal["use-luck", "resolve", "cancel"]


async def exposed_host(
    path: Path,
    backend: str = "sqlite",
    *,
    spec: HazardSpec | None = None,
    points: int = 15,
    modifiers: tuple[str, ...] = (),
    resistance: int = 0,
) -> tuple[str, PlayService, str]:
    cid, play = await fixture(path, backend, points=points, modifiers=modifiers, maximum_wait=10000)
    state = play._load(await play.store.read(cid))
    source = spec or flame()
    result = await HazardService(
        play, lambda *_: HazardContext(source, resistance=resistance)
    ).execute(
        cid,
        HazardCommand(
            id="exposed",
            actor_id="a",
            expected_revision=state.revision,
            kind="enter",
            hazard_id=source.id,
        ),
        principal_id="a",
    )
    return cid, play, result.schedule_id


async def prepare(
    play: PlayService,
    cid: str,
    schedule_id: str,
    *,
    secret: bool = False,
    identifier: str = "outside",
    exposure_command_id: str = "exposed",
) -> tuple[PrepareOutsideEvent, TaskResult]:
    state = play._load(await play.store.read(cid))
    command = PrepareOutsideEvent(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        schedule_id=schedule_id,
        source=NaturalExposureDeclaration(
            exposure_command_id=exposure_command_id, circumstances="Natural environmental exposure"
        ),
        secret=secret,
    )
    return command, await TaskService(play).execute(cid, command, principal_id="gm")


async def choose(
    play: PlayService,
    cid: str,
    pending_id: str | None,
    *,
    choice: Choice = "use-luck",
    identifier: str = "choose",
    principal: str | None = None,
) -> tuple[ChooseOutsideEvent, TaskResult]:
    assert pending_id is not None
    state = play._load(await play.store.read(cid))
    command = ChooseOutsideEvent(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending_id,
        choice=choice,
    )
    return command, await TaskService(play).execute(
        cid, command, principal_id=principal or ("a" if choice == "use-luck" else "gm")
    )


def private_result(state: PlayState, identifier: str = "choose") -> TaskResult:
    return TaskResult.model_validate_json(
        next(
            event.kind
            for event in state.resources.events
            if event.id == identity(RESULT_PREFIX, identifier)
        )
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "spec,damage",
    [
        (flame(), 1),
        (acid_spec("splash", id="acid", scene_id="dock", protection=HazardProtection()), 1),
        (acid_spec("immersion", id="acid", scene_id="dock", protection=HazardProtection()), 1),
    ],
)
async def test_original_is_pending_and_selected_faces_change_real_owner_injury_once(
    tmp_path: Path,
    backend: str,
    spec: HazardSpec,
    damage: int,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=spec)
    play.rng = RecordedDice((6,))
    original_command, begun = await prepare(play, cid, schedule_id)
    before = play._load(await play.store.read(cid))
    assert next(p.current for p in before.resources.pools if p.id == "hp:a") == 10
    assert before.resources.hazards[0].cycle == 0 and before.resources.hazards[0].due == 0
    assert begun.outside_event_json is not None
    assert OutsideEventOutcome.model_validate_json(begun.outside_event_json).dice == (6,)
    assert isinstance(snapshot(before).pending, OutsideEventPending)
    play.rng = RecordedDice((2, 4))
    command, result = await choose(play, cid, begun.pending_id)
    assert result.outside_event_json is not None
    selected = OutsideEventOutcome.model_validate_json(result.outside_event_json)
    assert selected.dice == (2,) and selected.basic_damage == damage
    assert selected.consequence is not None and selected.consequence.hp_lost == damage
    saved = await play.store.read(cid)
    after = play._load(saved)
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 10 - damage
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert after.resources.hazards[0].cycle == 1 and after.resources.hazards[0].due == 1
    assert snapshot(after).pending is None and len(snapshot(after).luck.receipts) == 1
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert await TaskService(restarted).execute(cid, original_command, principal_id="gm") == begun
    with pytest.raises(ConflictError):
        await choose(restarted, cid, begun.pending_id, identifier="too-late")
    assert saved == await restarted.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_furnace_selection_precedes_the_independent_major_wound_check(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=flame(3, 0))
    original_rng = RecordedDice((6, 6, 6))
    play.rng = original_rng
    _, begun = await prepare(play, cid, schedule_id)
    before = play._load(await play.store.read(cid))
    assert original_rng.exhausted()
    assert next(p.current for p in before.resources.pools if p.id == "hp:a") == 10
    consequence_rng = RecordedDice((2, 2, 2, 3, 3, 3, 3, 3, 3))
    play.rng = consequence_rng
    _, result = await choose(play, cid, begun.pending_id)
    assert result.luck and result.luck.attempts == ((6, 6, 6), (2, 2, 2), (3, 3, 3))
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.current == 4 and hp.injury is not None and not hp.injury.stunned
    assert consequence_rng.exhausted()
    assert after.resources.hazards[0].cycle == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_owner_accepts_visible_original_without_luck(tmp_path: Path, backend: str) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    play.rng = RecordedDice((4,))
    _, begun = await prepare(play, cid, schedule_id)
    play.rng = RecordedDice(())
    _, result = await choose(play, cid, begun.pending_id, choice="resolve", principal="a")
    assert result.luck is None and result.outside_event_json is not None
    selected = OutsideEventOutcome.model_validate_json(result.outside_event_json)
    assert selected.dice == (4,) and selected.basic_damage == 3
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 7
    assert not real_play_clock(state).cooldowns and not snapshot(state).luck.receipts


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("acid,resistance,injury", [(True, 0, 1), (False, 0, 1), (False, 1, 0)])
async def test_lowest_face_is_one_basic_damage_before_armor(
    tmp_path: Path,
    backend: str,
    acid: bool,
    resistance: int,
    injury: int,
) -> None:
    spec = (
        acid_spec("splash", id="acid", scene_id="dock", protection=HazardProtection())
        if acid
        else flame(1, -3)
    )
    cid, play, schedule_id = await exposed_host(tmp_path, backend, spec=spec, resistance=resistance)
    play.rng = RecordedDice((6,))
    _, begun = await prepare(play, cid, schedule_id)
    play.rng = RecordedDice((1, 2))
    _, result = await choose(play, cid, begun.pending_id)
    assert result.outside_event_json is not None
    selected = OutsideEventOutcome.model_validate_json(result.outside_event_json)
    assert selected.dice == (1,) and selected.basic_damage == 1
    assert selected.consequence and selected.consequence.hp_lost == injury
    after = play._load(await play.store.read(cid))
    assert snapshot(after).luck.rolls[-1].chosen_total == 1
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 10 - injury
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_secret_outside_choices_are_opaque_and_seed_reexecute_from_actual_exposure(
    tmp_path: Path,
    backend: str,
    choice: Choice,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    owner = await TaskService(play).pending(cid, principal_id="a")
    assert owner and owner.pending_id == begun.pending_id and owner.outside_event_json is None
    runtime = build_runtime(play)
    orchestrator = build_orchestrator(runtime, FakeProvider())
    for stage in ("pending", "completed"):
        if stage == "completed":
            _, result = await choose(play, cid, begun.pending_id, choice=choice)
            assert result.secret
            if choice == "use-luck":
                assert result.outside_event_json is None and result.luck is None
        context, _, _ = await orchestrator.context(cid, "a", "a")
        projection = json.dumps(await runtime.read(cid, principal_id="a"))
        stream = json.dumps(
            [entry.model_dump(mode="json") for entry in await runtime.events(cid, principal_id="a")]
        )
        for private_marker in (
            '"attempts"',
            '"chosen_index"',
            '"actor_json"',
            '"original"',
            "task-host:",
            "task-result:",
        ):
            assert (
                private_marker not in context
                and private_marker not in projection
                and private_marker not in stream
            )
    state = play._load(await play.store.read(cid))
    private = private_result(state)
    if choice == "use-luck":
        assert private.luck and private.luck.attempts == ((3,), (6,), (6,))
        assert private.luck.chosen_index == 0
    expected = 0 if choice == "cancel" else 2
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10 - expected
    assert state.resources.hazards[0].cycle == (choice != "cancel")
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_immediate_boundary_wrong_owner_current_authority_and_stale_choice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    before = await play.store.read(cid)
    invalid = PrepareOutsideEvent(
        id="wrong",
        actor_id="b",
        expected_revision=before["revision"],
        schedule_id=schedule_id,
        source=NaturalExposureDeclaration(
            exposure_command_id="exposed", circumstances="Natural environmental exposure"
        ),
    )
    with pytest.raises(ValidationError, match="does not affect"):
        await TaskService(play).execute(cid, invalid, principal_id="gm")
    assert before == await play.store.read(cid)
    play.rng = RecordedDice((6,))
    _, begun = await prepare(play, cid, schedule_id)
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="pending task roll"):
        await play.execute(
            cid,
            Wait(id="later-roll", actor_id="b", expected_revision=before["revision"], ticks=1),
            principal_id="b",
        )
    for principal in ("b", "gm"):
        with pytest.raises(AuthorizationError):
            await choose(play, cid, begun.pending_id, principal=principal)
    with pytest.raises(ValidationError, match="already rolled"):
        await choose(play, cid, begun.pending_id, choice="cancel")
    stale = ChooseOutsideEvent(
        id="stale",
        actor_id="a",
        expected_revision=before["revision"] - 1,
        pending_id=begun.pending_id or "",
        choice="use-luck",
    )
    with pytest.raises(ConflictError):
        await TaskService(play).execute(cid, stale, principal_id="a")
    assert before == await play.store.read(cid)
    play.rng = RecordedDice((2, 4))
    command, _ = await choose(play, cid, begun.pending_id)
    await change(play, cid, revoke_owner)
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="a")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_commit_rolls_back_injury_cooldown_schedule_and_choice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    before = await play.store.read(cid)
    events, history = await play.store.stream(cid), await play.store.history(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((6, 2, 4)), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose(failing, cid, begun.pending_id)
    assert before == await play.store.read(cid) == await play.store.replay(cid)
    assert events == await play.store.stream(cid) and history == await play.store.history(cid)
    play.rng = RecordedDice((6, 2, 4))
    await choose(play, cid, begun.pending_id)
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    assert len(real_play_clock(state).cooldowns) == 1 and state.resources.hazards[0].cycle == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_independent_store_race_and_changed_retry_payload(
    tmp_path: Path, backend: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    play.rng = secrets
    second = build_play(tmp_path, play.engine, backend=backend, instants=play.instants)
    before = play._load(await play.store.read(cid))
    first = ChooseOutsideEvent(
        id="first",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=begun.pending_id or "",
        choice="use-luck",
    )
    other = first.model_copy(update={"id": "other", "choice": "resolve"})
    results = await asyncio.gather(
        TaskService(play).execute(cid, first, principal_id="a"),
        TaskService(second).execute(cid, other, principal_id="gm"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.resources.hazards[0].cycle == 1 and snapshot(state).pending is None
    selected = other if isinstance(results[0], Exception) else first
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            selected.model_copy(update={"pending_id": "different"}),
            principal_id="gm" if selected == other else "a",
        )
    assert saved == await play.store.read(cid) == await play.store.replay(cid)
