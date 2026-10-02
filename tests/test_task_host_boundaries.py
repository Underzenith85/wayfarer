"""Immediate B66 opportunities are private, atomic, current-authority and replayable."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_task_host import begin_check, choose, fixture

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.events import visible
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.task_records import (
    ChooseTaskCheck,
    SetRealPlayClock,
    has_task_records,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_task_seed_reexecution_compares_complete_state(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, original = await begin_check(play, cid)
    command, result = await choose(play, cid, original.pending_id)
    saved = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    command_ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in command_ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == saved == await play.store.replay(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert await restarted.store.read(cid) == saved


@pytest.mark.parametrize("offset", [-1, 0])
async def test_cooldown_at_original_prevents_waiting_and_preserves_microseconds(
    tmp_path: Path, offset: int
) -> None:
    cid, play = await fixture(tmp_path)
    clock = real_play_clock(play._load(await play.store.read(cid)))
    assert clock.observed_at_us is not None
    origin = clock.observed_at_us
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    play.rng = RecordedDice((6, 6, 6))
    _, first = await begin_check(play, cid)
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    await choose(play, cid, first.pending_id)
    after = play._load(await play.store.read(cid))
    assert real_play_clock(after).cooldowns[0].available_at_microseconds == 3600900001
    now[0] = origin + 3600900001 + offset
    play.rng = RecordedDice((6, 6, 6))
    _, second = await begin_check(play, cid, identifier="second")
    before = await play.store.read(cid)
    if offset < 0:
        now[0] += 10_000_000
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="when this original was rolled"):
            await choose(play, cid, second.pending_id, identifier="waited")
        assert await play.store.read(cid) == before
        await choose(play, cid, second.pending_id, luck=False, identifier="accept")
    else:
        play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
        await choose(play, cid, second.pending_id, identifier="at-boundary")
        current = play._load(await play.store.read(cid))
        assert real_play_clock(current).cooldowns[0].available_at_microseconds == 7200900001


async def test_pending_pause_resume_has_no_dice_and_does_not_reset_original_time(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    initial = snapshot(play._load(await play.store.read(cid))).pending
    play.rng = RecordedDice(())
    for identifier, running in (("pause", False), ("resume-again", True)):
        state = play._load(await play.store.read(cid))
        command = SetRealPlayClock(
            id=identifier, actor_id="gm", expected_revision=state.revision, running=running
        )
        with pytest.raises(ValidationError, match="director|GM"):
            await TaskService(play).execute(cid, command, principal_id="a")
        await TaskService(play).execute(cid, command, principal_id="gm")
        assert snapshot(play._load(await play.store.read(cid))).pending == initial
    await choose(play, cid, original.pending_id, luck=False)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_duplicate_race_is_exact_and_changed_payload_is_rejected(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    assert original.pending_id is not None
    pending = play._load(await play.store.read(cid))
    command = ChooseTaskCheck(
        id="choice",
        actor_id="a",
        expected_revision=pending.revision,
        kind="use-luck",
        pending_id=original.pending_id,
    )
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    first, second = await asyncio.gather(
        *(TaskService(play).execute(cid, command, principal_id="a") for _ in range(2))
    )
    assert first == second
    saved = await play.store.read(cid)
    assert len(snapshot(play._load(saved)).luck.receipts) == 1
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid, command.model_copy(update={"kind": "accept-check"}), principal_id="a"
        )
    assert await play.store.read(cid) == saved


async def test_failed_random_resolution_rolls_back_luck_clock_and_consequences(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice((2, 2, 3))
    with pytest.raises(ValidationError):
        await choose(play, cid, original.pending_id)
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    _, result = await choose(play, cid, original.pending_id)
    assert result.action and result.action.revealed_fact_ids == ("clue",)


async def test_current_control_applies_to_new_choices_and_prior_retry(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await choose(play, cid, original.pending_id, principal="b")
    command, result = await choose(play, cid, original.pending_id, luck=False)
    assert result.check and result.check.total == 18

    def revoke(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "members": tuple(
                    member.model_copy(update={"actor_ids": ()})
                    if member.principal_id == "a"
                    else member
                    for member in state.members
                )
            }
        )

    await change(play, cid, revoke)
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="a")


async def test_secret_task_events_have_no_actor_visible_dice(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid, secret=True)
    await choose(play, cid, original.pending_id, luck=False, principal="gm")
    state = play._load(await play.store.read(cid))
    member = next(member for member in state.members if member.principal_id == "a")
    stream = await play.store.stream(cid)
    actor_events = [event.event for event in stream if visible(event.event, member)]
    assert all(
        "task-host:" not in event.model_dump_json() and '"dice"' not in event.model_dump_json()
        for event in actor_events
    )


async def test_zero_fp_inspection_commits_failed_exertion_without_original(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)

    def exhausted(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            pool.model_copy(update={"current": 0}) if pool.id == "fp:a" else pool
                            for pool in state.resources.pools
                        )
                    }
                )
            }
        )

    await change(play, cid, exhausted)
    play.rng = RecordedDice((6, 5, 5))
    _, result = await begin_check(play, cid)
    state = play._load(await play.store.read(cid))
    assert result.status == "interrupted" and result.check is None
    assert state.resources.game_time == 0 and snapshot(state).pending is None
    fp = next(pool for pool in state.resources.pools if pool.id == "fp:a")
    assert fp.fatigue and fp.fatigue.collapsed
    assert not state.world.knowledge and not snapshot(state).luck.rolls
    assert any(event.id.startswith("fatigue:combat-exertion:") for event in state.resources.events)


async def test_genesis_cannot_forge_task_cooldown_or_progress(tmp_path: Path) -> None:
    from test_actions import campaign

    from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
    from wayfarer.orchestration.task_records import PRIVATE_PREFIXES

    cid, play = await fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    for prefix in PRIVATE_PREFIXES:
        with pytest.raises(ValidationError, match="cannot seed"):
            play.initial_state(
                campaign(play.engine),
                state.world,
                ResourceState(
                    events=(ResourceEvent(id=prefix + "forged", at=0, target_id="a", kind="{}"),)
                ),
                (),
            )


@pytest.mark.parametrize(("points", "seconds"), [(15, 3600), (30, 1800), (60, 600)])
async def test_current_approved_tier_sets_actual_host_cooldown(
    tmp_path: Path, points: int, seconds: int
) -> None:
    cid, play = await fixture(tmp_path, points=points)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    _, result = await choose(play, cid, original.pending_id)
    state = play._load(await play.store.read(cid))
    clock = real_play_clock(state)
    assert result.luck and result.luck.available_at - result.luck.real_time == seconds
    assert (
        clock.cooldowns[0].available_at_microseconds
        == clock.elapsed_microseconds + seconds * 1_000_000
    )


async def test_lost_approval_prevents_new_luck_but_gm_can_accept_captured_original(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((3, 3, 3))
    _, original = await begin_check(play, cid)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) if actor.actor_id == "a" else actor
                    for actor in state.actors
                )
            }
        ),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await choose(play, cid, original.pending_id)
    assert await play.store.read(cid) == before
    _, result = await choose(play, cid, original.pending_id, luck=False, principal="gm")
    assert (
        result.check == original.check
        and result.action
        and result.action.revealed_fact_ids == ("clue",)
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_independent_store_race_rejects_second_distinct_choice(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    assert original.pending_id is not None
    revision = play._load(await play.store.read(cid)).revision
    other = build_play(tmp_path, play.engine, backend=backend, instants=play.instants)
    play.rng = secrets
    commands = [
        ChooseTaskCheck(
            id=identifier,
            actor_id="a",
            expected_revision=revision,
            kind="use-luck",
            pending_id=original.pending_id,
        )
        for identifier in ("one", "two")
    ]
    results = await asyncio.gather(
        TaskService(play).execute(cid, commands[0], principal_id="a"),
        TaskService(other).execute(cid, commands[1], principal_id="a"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    state = play._load(await play.store.read(cid))
    assert state.revision == revision + 1 and len(snapshot(state).luck.receipts) == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_paid_ordinary_fatigue_is_not_charged_again_at_choice(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, fatigue_cost=2)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 8
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    await choose(play, cid, original.pending_id)
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 8


def test_task_boundary_uses_actual_records_and_preserves_legacy_presence_markers() -> None:
    assert not has_task_records("{}")
    for invalid in ("[]", "true", "legacy", "null"):
        with pytest.raises(ValueError):
            has_task_records(invalid)
    assert not has_task_records('{"description":"task-host: prose","resources":{}}')
    assert not has_task_records('{"resources":{"events":[{"id":"unrelated"}]}}')
    assert has_task_records('{"resources":{"events":[{"id":"task-host:actual"}]}}')
    assert has_task_records(r'{"resources":{"events":[{"id":"task\u002dhost:escaped"}]}}')
