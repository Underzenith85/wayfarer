"""B345 floors conserve B346 elapsed work and mandatory overtime checks."""

import json
from decimal import Decimal
from functools import partial
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelValidationError
from support.runtime import build_play, build_runtime
from test_live_skill_prerequisite_context import reexecute
from test_long_task_phases import begin as phase_begin
from test_staged_minimum_skill import fixture
from test_task_host import choose

from wayfarer.engine.rules.checks import RecordedDice, draw_dice
from wayfarer.engine.rules.randomness import RNG_ALGORITHM, SeededRandom
from wayfarer.engine.simulation.campaign._task_phases import (
    SPENT_PREFIX,
    UNAVAILABLE_PREFIX,
    LongTaskPreparation,
    ensure_long_task_available,
    reserve_spent_shift,
)
from wayfarer.engine.simulation.campaign.activities import LongTaskRule
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.task_records import BeginTaskWork, BindLongTask, TaskPending, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.events import CommandRecord, payload_digest


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("luck", [False, True])
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_unavailable_supervision_preserves_overtime_and_worker_floor(
    tmp_path: Path, backend: str, luck: bool, worker_fails: bool
) -> None:
    initial, play = await fixture(tmp_path, backend, target=12, low_supervisor=True)
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind",
            actor_id="a",
            expected_revision=0,
            rule=LongTaskRule(
                id="bridge",
                target_id="skill:carpentry",
                required_man_hours=100,
                time_spent_modifier=-9,
            ),
            location_id="dock",
            supervisor_skill_id="skill:administration",
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice((1, 1, 3))
    begun = await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id="shift",
            actor_id="a",
            expected_revision=1,
            task_id="bridge",
            seconds=18 * 3600,
            supervisor_actor_id="b",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, TaskPending) and pending.actor_id == "b"
    assert pending.original.effective_target == 2 and pending.original.total == 5
    assert begun.pending_id == pending.id
    # A selected HT failure costs 3 FP and reduces actual Administration 5 to 2.
    # Optional supervision now has no lawful roll, but worker HT is still due.
    play.rng = RecordedDice(
        ((1, 1, 3) * 2 if luck else ()) + ((1, 1, 3) if worker_fails else (1, 1, 1))
    )
    sup_command, sup_result = await choose(
        play, cid, pending.id, actor="b", luck=luck, identifier="supervisor-ht"
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 7
    pending = snapshot(state).pending
    assert isinstance(pending, TaskPending) and pending.actor_id == "a"
    assert pending.original.effective_target == 2
    prepared = LongTaskPreparation.model_validate_json(pending.preparation_json)
    assert prepared.supervision_unavailable and prepared.supervision is None
    assert prepared.minimum_skill_generation == 1 and prepared.phase == "overtime"
    assert any(e.id.startswith(UNAVAILABLE_PREFIX) for e in state.resources.events)
    runtime = build_runtime(play)
    projection = json.dumps(await runtime.read(cid, principal_id="a"))
    stream = json.dumps(
        [e.model_dump(mode="json") for e in await runtime.events(cid, principal_id="a")]
    )
    for private in (
        UNAVAILABLE_PREFIX,
        "campaign-long-task-spent-v1:",
        "minimum_skill_generation",
        "supervision_unavailable",
        "preparation_json",
    ):
        assert private not in projection and private not in stream

    assert (sup_result.luck is not None) == luck
    if luck:
        assert sup_result.luck is not None and sup_result.luck.attempts == ((1, 1, 3),) * 3
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await TaskService(restarted).execute(cid, sup_command, principal_id="b") == sup_result

    assert await TaskService(play).execute(cid, sup_command, principal_id="b") == sup_result
    play.rng = RecordedDice(()) if worker_fails else RecordedDice((1, 1, 1))
    worker_command, result = await choose(play, cid, pending.id, luck=False, identifier="worker-ht")
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 18 * 3600
    if worker_fails:
        assert result.status == "interrupted" and result.activity is None
        assert snapshot(state).pending is None
        assert not any(e.id.startswith("campaign-activity:") for e in state.resources.events)
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
        with pytest.raises(ValidationError):
            await TaskService(play).execute(
                cid,
                BeginTaskWork(
                    id="second-shift",
                    actor_id="a",
                    expected_revision=state.revision,
                    task_id="bridge",
                    seconds=3600,
                ),
                principal_id="a",
            )
    else:
        pending = snapshot(state).pending
        assert isinstance(pending, TaskPending) and pending.actor_id == "a"
        assert pending.original.effective_target == pending.original.total == 3
        play.rng = RecordedDice(())
        _, finished = await choose(play, cid, pending.id, luck=False, identifier="worker-skill")
        assert finished.activity is not None and finished.activity.progress == Decimal(27)
        assert len(finished.activity.checks) == 3
    assert await TaskService(play).execute(cid, worker_command, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("worker_fails", [False, True])
async def test_unavailable_supervision_reexecutes_original_producers(
    tmp_path: Path, backend: str, worker_fails: bool
) -> None:
    initial, play = await fixture(tmp_path, backend, target=12, low_supervisor=True)
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind",
            actor_id="a",
            expected_revision=0,
            rule=LongTaskRule(
                id="bridge",
                target_id="skill:carpentry",
                required_man_hours=100,
                time_spent_modifier=-9,
            ),
            location_id="dock",
            supervisor_skill_id="skill:administration",
        ),
        principal_id="gm",
    )
    seeds = {
        total: next(
            f"{i:064x}"
            for i in range(1, 50000)
            if sum(draw_dice(SeededRandom(f"{i:064x}"))) == total
        )
        for total in (3, 5)
    }
    play.seeds = lambda: seeds[5]
    begun = await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id="shift",
            actor_id="a",
            expected_revision=1,
            task_id="bridge",
            seconds=18 * 3600,
            supervisor_actor_id="b",
        ),
        principal_id="gm",
    )
    assert begun.pending_id
    phases = (
        ("supervisor-ht", "worker-ht")
        if worker_fails
        else ("supervisor-ht", "worker-ht", "worker-skill")
    )
    for identifier in phases:
        play.seeds = partial(str, seeds[5 if worker_fails and identifier == "supervisor-ht" else 3])
        pending = snapshot(play._load(await play.store.read(cid))).pending
        assert isinstance(pending, TaskPending)
        _, result = await choose(
            play, cid, pending.id, luck=False, identifier=identifier, actor=pending.actor_id
        )
    if worker_fails:
        assert result.status == "interrupted" and result.activity is None
        final = play._load(await play.store.read(cid))
        assert [p.current for p in final.resources.pools if p.id in {"fp:a", "fp:b"}] == [7, 7]
        assert not any(e.id.startswith("campaign-activity:") for e in final.resources.events)
    else:
        assert result.activity is not None and result.activity.progress == Decimal(27)
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("worker_points", [1, 2])
async def test_intake_bounds_require_a_possible_actual_lawful_worker_check(
    tmp_path: Path, backend: str, worker_points: int
) -> None:
    initial, play = await fixture(tmp_path, backend, target=12, worker_points=worker_points)
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind",
            actor_id="a",
            expected_revision=0,
            rule=LongTaskRule(
                id="bridge",
                target_id="skill:carpentry",
                required_man_hours=100,
                time_spent_modifier=-9,
            ),
            location_id="dock",
            supervisor_skill_id="skill:administration",
        ),
        principal_id="gm",
    )
    command = BeginTaskWork(
        id="shift",
        actor_id="a",
        expected_revision=1,
        task_id="bridge",
        seconds=8 * 3600,
        supervisor_actor_id="b",
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(()) if worker_points == 1 else RecordedDice((1, 1, 1))
    if worker_points == 1:
        # Carpentry9−9+maximum lawful critical supervision2 remains only2.
        with pytest.raises(ValidationError, match="possible effective skill must be at least 3"):
            await TaskService(play).execute(cid, command, principal_id="gm")
        assert await play.store.read(cid) == before
        assert await play.store.command_input(cid, command.id) is None
        assert play.rng.exhausted()
        return
    begun = await TaskService(play).execute(cid, command, principal_id="gm")
    assert begun.pending_id
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, TaskPending) and pending.original.effective_target == 12
    play.rng = RecordedDice((1, 1, 1))
    await choose(play, cid, pending.id, actor="b", luck=False, identifier="supervision")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    # Carpentry10−9+actual critical supervision2 is a lawful3.
    assert isinstance(pending, TaskPending) and pending.original.effective_target == 3
    play.rng = RecordedDice(())
    _, result = await choose(play, cid, pending.id, luck=False, identifier="worker")
    assert result.activity is not None and result.activity.progress == Decimal(12)
    assert [c.effective_target for c in result.activity.checks] == [12, 3]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_shift_pending_retains_absent_floor_on_fresh_choice(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, target=12, worker_points=4)
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind",
            actor_id="a",
            expected_revision=0,
            rule=LongTaskRule(
                id="bridge",
                target_id="skill:carpentry",
                required_man_hours=100,
                time_spent_modifier=-9,
            ),
            location_id="dock",
        ),
        principal_id="gm",
    )
    command = BeginTaskWork(
        id="shift", actor_id="a", expected_revision=1, task_id="bridge", seconds=8 * 3600
    )
    encoded = json.dumps(
        {
            "operation": "task-host",
            "principal_id": "a",
            "command": command.model_dump(mode="json"),
            "skill_prerequisite_context_generation": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    historical = CommandRecord(
        campaign_id=cid,
        command_id=command.id,
        actor_id="a",
        expected_revision=1,
        resulting_revision=2,
        payload_hash=payload_digest({"input": encoded}),
        rules_version="1",
        event={"action": "resource", "outcome": "historical envelope diagnostic"},
        state_after=initial,
        entropy_seed=f"{1:064x}",
        rng_algorithm=RNG_ALGORITHM,
        recorded_at_us=1_700_000_000_000_000,
        command_input=encoded,
    )
    with replay_inputs(historical):
        begun = await TaskService(play).execute(cid, command, principal_id="a")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, TaskPending) and begun.pending_id == pending.id
    assert pending.original.effective_target == 2
    prepared = LongTaskPreparation.model_validate_json(pending.preparation_json)
    assert prepared.minimum_skill_generation is None
    assert "minimum_skill_generation" not in prepared.model_dump()
    _, result = await choose(play, cid, pending.id, luck=False, identifier="worker")
    assert result.status == "completed" and result.activity is not None
    assert result.check is not None and result.check.effective_target == 2
    await reexecute(initial, play, tmp_path / "reexecute")


def test_private_spent_shift_reserves_elapsed_day_without_a_selected_roll() -> None:
    # A component seam: this constructs elapsed resources, not a public shift producer.
    state, preparation = phase_begin()
    saved = reserve_spent_shift(state, "interrupted", ("a", "b"), started_at=0)
    assert saved.revision == state.revision and saved.game_time == state.game_time
    assert len(saved.events) == 2 and all(e.id.startswith(SPENT_PREFIX) for e in saved.events)
    assert reserve_spent_shift(saved, "interrupted", ("a", "b"), started_at=0) == saved
    assert preparation.worker is None
    for actor in ("a", "b"):
        with pytest.raises(ValidationError):
            ensure_long_task_available(saved, actor, started_at=8 * 3600, seconds=3600)
        ensure_long_task_available(saved, actor, started_at=86400, seconds=3600)


def test_private_minimum_generation_is_exact_and_legacy_json_omits_it() -> None:
    _, preparation = phase_begin()
    encoded = preparation.model_dump(mode="json")
    assert "minimum_skill_generation" not in encoded and "supervision_unavailable" not in encoded
    for invalid in (False, True, 0, 2, "1", 1.0):
        with pytest.raises(ModelValidationError, match="Unsupported long-task minimum-skill"):
            LongTaskPreparation.model_validate_json(
                json.dumps({**encoded, "minimum_skill_generation": invalid})
            )
    current = LongTaskPreparation.model_validate_json(
        json.dumps({**encoded, "minimum_skill_generation": 1})
    )
    assert current.minimum_skill_generation == 1
    with pytest.raises(ModelValidationError, match="Unavailable supervision"):
        LongTaskPreparation.model_validate(
            current.model_copy(update={"supervision_unavailable": True})
        )
