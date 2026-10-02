"""B346 phase effects survive real store commits before later original rolls."""

import secrets
from decimal import Decimal
from pathlib import Path

import pytest
from test_combat_sensory_authority import change
from test_task_host import choose, fixture

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.symptoms import SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.activities import LongTaskRule
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import BeginTaskWork, BindLongTask, TaskResult, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def bind_work(play: PlayService, cid: str, *, supervised: bool = False) -> None:
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind-work",
            actor_id="a",
            expected_revision=state.revision,
            rule=LongTaskRule(id="bridge", target_id="skill:carpentry", required_man_hours=100),
            location_id="dock",
            supervisor_skill_id="skill:administration" if supervised else None,
        ),
        principal_id="gm",
    )


async def begin_work(
    play: PlayService,
    cid: str,
    *,
    hours: int = 8,
    identifier: str = "shift",
    supervised: bool = False,
) -> None:
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id=identifier,
            actor_id="a",
            expected_revision=state.revision,
            task_id="bridge",
            seconds=hours * 3600,
            supervisor_actor_id="b" if supervised else None,
        ),
        principal_id="gm" if supervised else "a",
    )


async def accept_current(
    play: PlayService, cid: str, identifier: str, *, luck: bool = False
) -> TaskResult:
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert pending is not None
    return (
        await choose(
            play, cid, pending.id, luck=luck, identifier=identifier, actor=pending.actor_id
        )
    )[1]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_supervised_overtime_commits_separate_fp_and_selected_targets(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await bind_work(play, cid, supervised=True)
    play.rng = RecordedDice((5, 5, 5))
    await begin_work(play, cid, hours=10, supervised=True)
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert pending and pending.actor_id == "b" and pending.original.effective_target == 10
    play.rng = RecordedDice((2, 2, 2))
    await accept_current(play, cid, "supervisor-ht")
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:b") == 5
    pending = snapshot(state).pending
    assert pending and pending.actor_id == "b" and pending.original.effective_target == 7
    play.rng = RecordedDice((4, 4, 5))
    await accept_current(play, cid, "supervisor-skill")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert pending and pending.actor_id == "a" and pending.original.effective_target == 10
    play.rng = RecordedDice((3, 3, 4))
    await accept_current(play, cid, "worker-ht")
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 7
    pending = snapshot(state).pending
    assert pending and pending.actor_id == "a" and pending.original.effective_target == 10
    play.rng = RecordedDice(())
    result = await accept_current(play, cid, "worker-skill")
    assert result.activity and result.activity.progress == Decimal(10)
    assert play._load(await play.store.read(cid)).resources.game_time == 36000
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_actual_later_day_keeps_destroyed_prior_work(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    await bind_work(play, cid)
    for day, worker_dice, followup in (
        (0, (3, 3, 3), ()),
        (1, (6, 6, 6), (3, 4)),
        (2, (3, 3, 3), ()),
    ):
        play.rng = RecordedDice((1, 1, 1))
        await begin_work(play, cid, hours=24, identifier=f"shift-{day}")
        play.rng = RecordedDice(worker_dice)
        await accept_current(play, cid, f"ht-{day}")
        play.rng = RecordedDice(followup)
        result = await accept_current(play, cid, f"work-{day}")
        assert (
            result.activity
            and result.activity.total_progress == (Decimal(24), Decimal(17), Decimal(41))[day]
        )
        assert result.activity.ruined_hours == (7 if day == 1 else 0)
    assert play._load(await play.store.read(cid)).resources.game_time == 259200


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_supervised_work_full_seed_reexecution(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    await bind_work(play, cid, supervised=True)
    await begin_work(play, cid, hours=10, supervised=True)
    for phase in range(4):
        result = await accept_current(play, cid, f"phase-{phase}")
    assert result.activity is not None
    saved = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute-work"),
    )
    assert len(checks) == 6 and all(check.folded and check.reexecuted for check in checks)
    assert saved == replayed == await play.store.replay(cid)


async def test_next_worker_original_uses_current_symptoms_without_rescoring_prior_ht(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    await bind_work(play, cid)
    play.rng = RecordedDice((4, 4, 5))
    await begin_work(play, cid, hours=10)
    before = snapshot(play._load(await play.store.read(cid))).pending
    assert before and before.original.effective_target == 10

    def symptoms(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "symptom_effects": (
                            SymptomEffect(
                                id="iq-loss",
                                pool_id="hp:a",
                                actor_id="a",
                                source_id="spell",
                                spec=SymptomSpec(kind="attribute-penalty", attribute="iq", level=2),
                                active=True,
                            ),
                            SymptomEffect(
                                id="cough",
                                pool_id="hp:a",
                                actor_id="a",
                                source_id="illness",
                                spec=SymptomSpec(kind="coughing"),
                                active=True,
                            ),
                        )
                    }
                )
            }
        )

    await change(play, cid, symptoms)
    play.rng = RecordedDice((2, 2, 3))
    selected = await accept_current(play, cid, "overtime")
    assert selected.check == before.original
    state = play._load(await play.store.read(cid))
    worker = snapshot(state).pending
    assert worker and worker.original.base_target == 10
    assert worker.original.effective_target == 6  # IQ -2, coughing -1, prior HT failure -3.
    assert any(modifier.reason == "Symptoms coughing" for modifier in worker.original.modifiers)
    play.rng = RecordedDice(())
    result = await accept_current(play, cid, "work")
    assert result.activity and result.activity.progress == Decimal(4)
