"""Outside-event declarations use the existing exact real-play and authority lock."""

import os
from pathlib import Path

import pytest
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import RevokingStore
from test_outside_event_damage import flame
from test_outside_event_host import choose, exposed_host, prepare
from test_secret_task_boundaries import RevokingPostgresStore, reapprove, revoke_owner, revoke_seat

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.hazards import HazardCommand
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.outside_event_records import ChooseOutsideEvent
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,seconds", [(15, 3600), (30, 1800), (60, 600)])
@pytest.mark.parametrize("secret", [False, True])
async def test_cooldown_original_guard_and_exact_unrolled_deadline(
    tmp_path: Path,
    backend: str,
    points: int,
    seconds: int,
    secret: bool,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend, points=points)
    origin = real_play_clock(play._load(await play.store.read(cid))).observed_at_us
    assert origin is not None
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    play.rng = RecordedDice((6,))
    _, first = await prepare(play, cid, schedule_id)
    play.rng = RecordedDice((2, 4))
    await choose(play, cid, first.pending_id)
    deadline = 900001 + seconds * 1_000_000
    assert (
        real_play_clock(play._load(await play.store.read(cid)))
        .cooldowns[0]
        .available_at_microseconds
        == deadline
    )
    state = play._load(await play.store.read(cid))
    spec = flame().model_copy(update={"id": "second-fire"})
    result = await HazardService(play, lambda *_: HazardContext(spec)).execute(
        cid,
        HazardCommand(
            id="second-exposure",
            actor_id="a",
            expected_revision=state.revision,
            kind="enter",
            hazard_id=spec.id,
        ),
        principal_id="a",
    )
    now[0] += 1
    play.rng = RecordedDice(()) if secret else RecordedDice((6,))
    _, second = await prepare(
        play,
        cid,
        result.schedule_id,
        secret=secret,
        identifier="second",
        exposure_command_id="second-exposure",
    )
    pending = snapshot(play._load(await play.store.read(cid))).pending
    for identifier, running, at in (("pause", False, 1_000_000), ("resume-again", True, 5_000_000)):
        now[0] = origin + at
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id=identifier, actor_id="gm", expected_revision=state.revision, running=running
            ),
            principal_id="gm",
        )
        assert snapshot(play._load(await play.store.read(cid))).pending == pending
    now[0] = origin + deadline + 4_000_000 - 1
    play.rng = RecordedDice(())
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="cooling down"):
        await choose(play, cid, second.pending_id, identifier="second-use")
    assert before == await play.store.read(cid)
    now[0] += 1
    if secret:
        play.rng = RecordedDice((6, 2, 4))
        await choose(play, cid, second.pending_id, identifier="second-use")
        assert (
            real_play_clock(play._load(await play.store.read(cid)))
            .cooldowns[0]
            .available_at_microseconds
            == deadline + seconds * 1_000_000
        )
    else:
        with pytest.raises(ValidationError, match="when this outside original was rolled"):
            await choose(play, cid, second.pending_id, identifier="second-use")
        assert before == await play.store.read(cid)
        await choose(play, cid, second.pending_id, choice="resolve", identifier="accept-original")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("retry", [False, True])
async def test_owner_revoked_after_outer_authorization_refuses_commit_or_retry(
    tmp_path: Path,
    backend: str,
    retry: bool,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    before = play._load(await play.store.read(cid))
    command = ChooseOutsideEvent(
        id="choice",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=begun.pending_id or "",
        choice="use-luck",
    )
    play.rng = RecordedDice((6, 2, 4))
    if retry:
        await TaskService(play).execute(cid, command, principal_id="a")
    store: RevokingPostgresStore | RevokingStore
    if backend == "postgres":
        store = RevokingPostgresStore(os.environ["WAYFARER_TEST_DATABASE_URL"], 10)
    else:
        store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    store.revoke = lambda: change(play, cid, revoke_owner)
    play.store = store
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ConflictError)):
        await TaskService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert len(snapshot(state).luck.receipts) == int(retry)
    assert state.resources.hazards[0].cycle == int(retry)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_context_changed_after_preparation_cannot_restore_original_hp(
    tmp_path: Path, backend: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    _, begun = await prepare(play, cid, schedule_id, secret=True)

    def hurt(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            pool.model_copy(update={"current": 7}) if pool.id == "hp:a" else pool
                            for pool in state.resources.pools
                        )
                    }
                )
            }
        )

    await change(play, cid, hurt)
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="context changed"):
        await choose(play, cid, begun.pending_id)
    assert before == await play.store.read(cid)
    await choose(play, cid, begun.pending_id, choice="cancel")
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:a"
        )
        == 7
    )


@pytest.mark.parametrize("modifiers", [("active",), ("defensive",), ("aspected-combat",)])
async def test_out_of_scope_variants_refuse_before_target_dice(
    tmp_path: Path, modifiers: tuple[str, ...]
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, modifiers=modifiers)
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="unmodified Luck"):
        await choose(play, cid, begun.pending_id)
    assert before == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_normal_resolution_and_cancellation_require_current_gm(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    prepared, begun = await prepare(play, cid, schedule_id, secret=True)
    before = await play.store.read(cid)
    for choice in ("resolve", "cancel"):
        with pytest.raises(ValidationError, match="director"):
            await choose(play, cid, begun.pending_id, choice=choice, principal="a")
    assert before == await play.store.read(cid)
    play.engine.reviewer.gm_ids = frozenset()
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).execute(cid, prepared, principal_id="gm")
    with pytest.raises(ValidationError, match="GM authority"):
        await choose(play, cid, begun.pending_id, choice="resolve")
    play.engine.reviewer.gm_ids = frozenset({"gm"})
    await change(play, cid, revoke_seat)
    with pytest.raises(ValidationError, match="director"):
        await choose(play, cid, begun.pending_id, choice="cancel")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_approval_removes_luck_before_secret_dice(
    tmp_path: Path, backend: str
) -> None:
    cid, play, schedule_id = await exposed_host(tmp_path, backend)
    _, begun = await prepare(play, cid, schedule_id, secret=True)
    await reapprove(play, cid, luck=False)
    before = await play.store.read(cid)
    with pytest.raises((ValidationError, ConflictError)):
        await choose(play, cid, begun.pending_id)
    assert before == await play.store.read(cid)
    await choose(play, cid, begun.pending_id, choice="cancel")
    state = play._load(await play.store.read(cid))
    assert not snapshot(state).luck.receipts and state.resources.hazards[0].cycle == 0
