"""B66 secret predeclaration selects actual private Inspect/Social consequences."""

import asyncio
import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_orchestrator, build_play, build_runtime
from test_task_host import fixture
from test_wave9 import FakeProvider

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.errors import ConflictError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import (
    RESULT_PREFIX,
    ChooseSecretTaskCheck,
    ChooseTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    TaskResult,
    identity,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands

Choice = Literal["use-luck", "resolve", "cancel"]


async def prepare(
    play: PlayService, cid: str, identifier: str = "secret"
) -> tuple[PrepareSecretTaskCheck, TaskResult]:
    state = play._load(await play.store.read(cid))
    command = PrepareSecretTaskCheck(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        check_id="carpentry-inspect",
    )
    return command, await TaskService(play).execute(cid, command, principal_id="gm")


async def choose_secret(
    play: PlayService,
    cid: str,
    pending_id: str | None,
    choice: Choice = "use-luck",
    *,
    identifier: str = "declare",
    principal: str | None = None,
) -> tuple[ChooseSecretTaskCheck, TaskResult]:
    assert pending_id is not None
    state = play._load(await play.store.read(cid))
    command = ChooseSecretTaskCheck(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending_id,
        choice=choice,
    )
    return command, await TaskService(play).execute(
        cid, command, principal_id=principal or ("a" if choice == "use-luck" else "gm")
    )


def private_result(state: PlayState, identifier: str = "declare") -> TaskResult:
    return TaskResult.model_validate_json(
        next(
            event.kind
            for event in state.resources.events
            if event.id == identity(RESULT_PREFIX, identifier)
        )
    )


def assert_opaque(result: TaskResult) -> None:
    assert result.secret
    assert result.check is None and result.luck is None
    assert result.action is None and result.activity is None
    assert result.reason == ""


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("action,fact", [("inspect", "clue"), ("social", "promise")])
async def test_owner_declaration_selects_private_actual_fact_once(
    tmp_path: Path, backend: str, action: Literal["inspect", "social"], fact: str
) -> None:
    cid, play = await fixture(tmp_path, backend, action=action, fatigue_cost=2)
    _, begun = await prepare(play, cid)
    prepared = play._load(await play.store.read(cid))
    pending = snapshot(prepared).pending
    assert isinstance(pending, SecretTaskPending)
    assert snapshot(prepared).luck.rolls[-1].original is None
    assert prepared.resources.game_time == 1 and not prepared.world.knowledge
    assert next(pool.current for pool in prepared.resources.pools if pool.id == "fp:a") == 8
    visible = await TaskService(play).pending(cid, principal_id="a")
    assert visible and visible.pending_id == begun.pending_id
    assert_opaque(visible)
    play.rng = RecordedDice((6, 6, 6, 5, 5, 5, 2, 2, 3))
    command, result = await choose_secret(play, cid, begun.pending_id)
    assert_opaque(result)
    assert result.status == "completed" and result.pending_id is None
    saved = await play.store.read(cid)
    after = play._load(saved)
    private = private_result(after)
    assert private.check and (
        private.check.effective_target,
        private.check.total,
        private.check.margin,
    ) == (12, 7, 5)
    assert private.check.outcome is Outcome.SUCCESS
    assert private.luck and private.luck.attempts == ((6, 6, 6), (5, 5, 5), (2, 2, 3))
    assert private.luck.chosen_index == 2
    assert private.action and private.action.revealed_fact_ids == (fact,)
    assert after.world.knowledge.count(("a", fact)) == 1
    assert after.resources.game_time == 1 and snapshot(after).pending is None
    assert next(pool.current for pool in after.resources.pools if pool.id == "fp:a") == 8
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert await restarted.store.read(cid) == saved == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,index,total,outcome,learned",
    [
        ((6, 6, 6, 6, 5, 6, 5, 5, 5), 2, 15, Outcome.FAILURE, False),
        ((6, 6, 6, 1, 1, 1, 2, 2, 3), 1, 3, Outcome.CRITICAL_SUCCESS, True),
        ((2, 2, 3, 1, 2, 4, 2, 3, 2), 0, 7, Outcome.SUCCESS, True),
    ],
)
async def test_independent_selection_oracles(
    tmp_path: Path,
    backend: str,
    dice: tuple[int, ...],
    index: int,
    total: int,
    outcome: Outcome,
    learned: bool,
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, begun = await prepare(play, cid)
    play.rng = RecordedDice(dice)
    await choose_secret(play, cid, begun.pending_id)
    state = play._load(await play.store.read(cid))
    result = private_result(state)
    assert result.luck and result.luck.chosen_index == index
    assert result.check and result.check.total == total and result.check.outcome is outcome
    assert (("a", "clue") in state.world.knowledge) is learned
    assert len(real_play_clock(state).cooldowns) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice,faces", [("resolve", (6, 6, 6)), ("cancel", ())])
async def test_gm_honest_continuations_keep_paid_cost_without_luck(
    tmp_path: Path, backend: str, choice: Choice, faces: tuple[int, ...]
) -> None:
    cid, play = await fixture(tmp_path, backend, fatigue_cost=2)
    command, begun = await prepare(play, cid)
    before = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError, match="pending task roll"):
        await play.execute(
            cid,
            Wait(id="blocked", actor_id="b", expected_revision=before.revision, ticks=1),
            principal_id="b",
        )
    with pytest.raises(ConflictError, match="pending task roll"):
        await prepare(play, cid, "another")
    with pytest.raises(ConflictError, match="no longer"):
        await TaskService(play).execute(
            cid,
            ChooseTaskCheck(
                id="wrong-kind",
                actor_id="a",
                expected_revision=before.revision,
                pending_id=begun.pending_id or "",
                kind="accept-check",
            ),
            principal_id="gm",
        )
    play.rng = RecordedDice(faces)
    chosen, result = await choose_secret(play, cid, begun.pending_id, choice)
    state = play._load(await play.store.read(cid))
    assert result.status == ("cancelled" if choice == "cancel" else "completed")
    assert not snapshot(state).luck.receipts and not real_play_clock(state).cooldowns
    assert snapshot(state).pending is None and not state.world.knowledge
    assert state.resources.game_time == 1
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 8
    saved = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, chosen, principal_id="gm") == result
    assert await TaskService(restarted).execute(cid, command, principal_id="gm") == begun
    assert await restarted.store.read(cid) == saved == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await choose_secret(play, cid, begun.pending_id, choice, identifier="late")
    play.rng = RecordedDice(())
    await prepare(play, cid, "next")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_seed_only_reexecution_and_all_private_projections(
    tmp_path: Path, backend: str, choice: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, begun = await prepare(play, cid)
    runtime = build_runtime(play)
    orchestrator = build_orchestrator(runtime, FakeProvider())
    for stage in ("pending", "completed"):
        if stage == "completed":
            await choose_secret(play, cid, begun.pending_id, choice)
        context, _, _ = await orchestrator.context(cid, "a", "a")
        projection = json.dumps(await runtime.read(cid, principal_id="a"))
        stream = json.dumps(
            [entry.model_dump(mode="json") for entry in await runtime.events(cid, principal_id="a")]
        )
        for private in (
            '"dice"',
            '"effective_target"',
            '"margin"',
            '"attempts"',
            "task-host:",
            "task-result:",
            "preparation_json",
        ):
            assert private not in context and private not in projection and private not in stream
    state = play._load(await play.store.read(cid))
    result = private_result(state)
    if choice == "use-luck":
        assert result.luck and result.luck.attempts == ((3, 6, 6), (1, 2, 4), (1, 4, 4))
        assert result.luck.chosen_index == 1
        assert result.check and (result.check.total, result.check.margin) == (7, 5)
    else:
        assert not state.world.knowledge
        assert result.luck is None
        assert (result.check.total if result.check else None) == (
            15 if choice == "resolve" else None
        )
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
@pytest.mark.parametrize("opponent", ["resolve", "cancel", "use-luck"])
async def test_independent_store_competing_choices_and_payload_identity(
    tmp_path: Path, backend: str, opponent: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, begun = await prepare(play, cid)
    assert begun.pending_id is not None
    before = play._load(await play.store.read(cid))
    play.rng = secrets
    other = build_play(tmp_path, play.engine, backend=backend, instants=play.instants)
    first = ChooseSecretTaskCheck(
        id="owner",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
    )
    second = first.model_copy(update={"id": "other", "choice": opponent})
    results = await asyncio.gather(
        TaskService(play).execute(cid, first, principal_id="a"),
        TaskService(other).execute(
            cid, second, principal_id="a" if opponent == "use-luck" else "gm"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.revision == before.revision + 1 and snapshot(state).pending is None
    assert len(snapshot(state).luck.receipts) == (
        1 if not isinstance(results[0], Exception) or opponent == "use-luck" else 0
    )
    command = first if not isinstance(results[0], Exception) else second
    principal = "a" if command.choice == "use-luck" else "gm"
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid, command.model_copy(update={"pending_id": "changed"}), principal_id=principal
        )
    assert saved == await play.store.read(cid) == await play.store.replay(cid)
