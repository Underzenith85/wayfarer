"""Authenticated physical admission, current ownership, casting and rollback."""

from pathlib import Path

import pytest
from support.analyze_magic import revision
from support.detect_magic import fixture, start, work
from test_actions import campaign
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup, Move, Wait
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.magic.detect_magic_state import (
    CancelDetectMagic,
    CompleteDetectMagic,
    DetectSubject,
    ObserveDetectMagicSubject,
    StartDetectMagic,
    casts,
    findings,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.orchestration.size_forms import SizeFormService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_deadline_control_stale_revision_and_pending_concentration_pre_rng(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    command = CompleteDetectMagic(
        id="too-soon",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="detection",
    )
    with pytest.raises(ConflictError, match="five"):
        await DetectMagicService(play).execute(cid, command, principal_id="cora")
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await DetectMagicService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await DetectMagicService(play).execute(
            cid,
            CancelDetectMagic(id="stale", actor_id="c", expected_revision=0, cast_id="detection"),
            principal_id="cora",
        )
    with pytest.raises(ConflictError):
        await play.execute(
            cid,
            Move(
                id="busy-move",
                actor_id="c",
                expected_revision=await revision(play, cid),
                destination_id="dock",
            ),
            principal_id="c",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
    await DetectMagicService(play).execute(
        cid,
        CancelDetectMagic(
            id="cancel",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="detection",
        ),
        principal_id="cora",
    )
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["detection"].status == "cancelled"
    assert not findings(state.resources)
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_subject_authentication_and_current_drop_reject_before_cast(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    subject = ObserveDetectMagicSubject(
        id="physical",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        subject=DetectSubject(
            id="physical",
            caster_id="c",
            target_id="cloak",
            carrier="inventory",
            backfire="injury-one",
        ),
    )
    service = DetectMagicService(play)
    saved = await play.store.read(cid)
    for principal in ("cora", "alice", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await service.execute(cid, subject, principal_id=principal)
    assert await play.store.read(cid) == saved
    await service.execute(cid, subject, principal_id="gm")
    await SizeFormService(play).retrieve(
        cid,
        WorldGroundCommand(
            id="drop-subject",
            actor_id="c",
            expected_revision=await revision(play, cid),
            kind="drop",
            item_id="cloak",
        ),
        principal_id="cora",
    )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        await service.execute(
            cid,
            StartDetectMagic(
                id="start-stale-subject",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="detection",
                subject_id="physical",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_elapsed_wait_is_not_committed_casting_work(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    result = await play.execute(
        cid,
        Wait(
            id="ordinary-wait", actor_id="c", expected_revision=await revision(play, cid), ticks=5
        ),
        principal_id="c",
    )
    assert result.status == "committed"
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["detection"].status == "cancelled"
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dice", [(1, 5, 3), (6, 5, 6)])
async def test_complete_failure_roll_fatigue_injury_and_receipt_rollback_together(
    tmp_path: Path,
    backend: str,
    dice: tuple[int, ...],
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    await work(play, cid)
    command = CompleteDetectMagic(
        id="finish", actor_id="c", expected_revision=await revision(play, cid), cast_id="detection"
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await DetectMagicService(failing).execute(cid, command, principal_id="cora")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice(dice)
    receipt = await DetectMagicService(play).execute(cid, command, principal_id="cora")
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await DetectMagicService(play).execute(cid, command, principal_id="cora") == receipt
    with pytest.raises(ConflictError):
        await DetectMagicService(play).execute(
            cid, command.model_copy(update={"cast_id": "changed"}), principal_id="cora"
        )
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_private_receipts_cannot_be_authored_genesis(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            state.resources.model_copy(
                update={
                    "revision": 0,
                    "events": (
                        ResourceEvent(id="detect-magic:forged", at=0, target_id="c", kind="{}"),
                    ),
                }
            ),
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
