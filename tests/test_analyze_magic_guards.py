"""Real authority, cost/deadline, interruption, false-report and CAS boundaries."""

from pathlib import Path

import pytest
from support.analyze_magic import complete, fixture, report, revision, start, work
from support.runtime import build_play, build_runtime
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Move, Wait
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalyzeSubject,
    CancelAnalyzeMagic,
    CompleteAnalyzeMagic,
    ObserveAnalyzeMagicSubject,
    ReportAnalyzeMagic,
    StartAnalyzeMagic,
    casts,
    secret_result,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.analyze_magic import AnalyzeMagicService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_actor_deadline_cancel_and_other_actor_free(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    complete_command = CompleteAnalyzeMagic(
        id="premature",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="analysis",
    )
    with pytest.raises(ConflictError, match="hour"):
        await AnalyzeMagicService(play).execute(cid, complete_command, principal_id="cora")
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await AnalyzeMagicService(play).execute(cid, complete_command, principal_id=principal)
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
    # Another actor may question the world without borrowing the commitment.
    from wayfarer.engine.simulation.actions import Inspect

    await play.execute(
        cid,
        Inspect(
            id="other-free",
            actor_id="b",
            expected_revision=await revision(play, cid),
            target_id="b",
        ),
        principal_id="b",
    )
    await AnalyzeMagicService(play).execute(
        cid,
        CancelAnalyzeMagic(
            id="cancel",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="analysis",
        ),
        principal_id="cora",
    )
    state = play._load(await play.store.read(cid))
    assert casts(state.resources)["analysis"].status == "cancelled"
    assert secret_result(state.resources, "analysis") is None
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dice", [(1, 5, 3), (6, 5, 6)])
async def test_roll_and_false_report_authority_cas_restart_retry(
    tmp_path: Path, backend: str, dice: tuple[int, ...]
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    await work(play, cid)
    command = CompleteAnalyzeMagic(
        id="finish", actor_id="c", expected_revision=await revision(play, cid), cast_id="analysis"
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(dice))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await AnalyzeMagicService(failing).execute(cid, command, principal_id="cora")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice(dice)
    receipt = await AnalyzeMagicService(play).execute(cid, command, principal_id="cora")
    assert play.rng.exhausted()
    committed = await play.store.read(cid)
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await AnalyzeMagicService(restart).execute(cid, command, principal_id="cora") == receipt
    with pytest.raises(ConflictError):
        await AnalyzeMagicService(restart).execute(
            cid, command.model_copy(update={"cast_id": "changed"}), principal_id="cora"
        )
    assert await play.store.read(cid) == committed
    report_command = ReportAnalyzeMagic(
        id="report",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        cast_id="analysis",
        claimed_power=24 if sum(dice) == 17 else None,
    )
    for principal in ("cora", "alice", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await AnalyzeMagicService(restart).execute(cid, report_command, principal_id=principal)
    with pytest.raises(ValidationError):
        await AnalyzeMagicService(restart).execute(
            cid,
            report_command.model_copy(update={"claimed_power": 22 if sum(dice) == 17 else 24}),
            principal_id="gm",
        )
    assert await play.store.read(cid) == committed
    await build_runtime(restart).submit_json(
        cid, report_command.model_dump(mode="json"), principal_id="gm"
    )
    assert (
        next(
            p.current
            for p in restart._load(await play.store.read(cid)).resources.pools
            if p.id == "fp:c"
        )
        == 2
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_daily_attempt_refusal_and_outside_clock_not_casting_work(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    await work(play, cid)
    await complete(play, cid, 48)
    await report(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="today"):
        await AnalyzeMagicService(play).execute(
            cid,
            StartAnalyzeMagic(
                id="retry-today",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="again",
                subject_id="analysis-physical",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
    # A separate actual campaign proves ordinary clock advancement is not credited.
    cid2, play2, _ = await fixture(tmp_path / "outside", backend)
    await start(play2, cid2)
    await play2.execute(
        cid2,
        Wait(
            id="outside-hour",
            actor_id="b",
            expected_revision=await revision(play2, cid2),
            ticks=3600,
        ),
        principal_id="b",
    )
    state = play2._load(await play2.store.read(cid2))
    cast = casts(state.resources)["analysis"]
    assert cast.seconds == 0 and cast.status == "cancelled"
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_physical_observation_authority_identity_and_current_owner(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    service = AnalyzeMagicService(play)
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    command = ObserveAnalyzeMagicSubject(
        id="physical-proof",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        subject=AnalyzeSubject(id="physical", caster_id="c", item_id="cloak"),
    )
    for principal in ("cora", "alice", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await service.execute(cid, command, principal_id=principal)
    with pytest.raises(ValidationError):
        await service.execute(
            cid,
            command.model_copy(
                update={"subject": command.subject.model_copy(update={"caster_id": "b"})}
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert play.rng.exhausted()
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="gm")
    observed = await play.store.read(cid)
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(
                update={"id": "duplicate", "expected_revision": await revision(play, cid)}
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == observed and play.rng.exhausted()
