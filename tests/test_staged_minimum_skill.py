"""B345 admission of staged originals before exertion, cost, time and dice."""

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_drowsy_toxins import produce as produce_drowsy
from test_live_skill_prerequisite_context import reexecute
from test_task_host import fixture as source_fixture

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice, draw_dice
from wayfarer.engine.rules.randomness import RNG_ALGORITHM, SeededRandom
from wayfarer.engine.simulation.action_engine.engine import ActionEngine, TaskCheckPreparation
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.task_records import (
    BeginTaskCheck,
    ChooseSecretTaskCheck,
    ChooseTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    TaskPending,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.events import CommandRecord, payload_digest

Kind = Literal["inspect", "social"]
Mode = Literal["ordinary", "gm-secret", "unrolled-secret"]


async def fixture(
    path: Path,
    backend: str,
    *,
    target: int,
    kind: Kind = "inspect",
    low_supervisor: bool = False,
    worker_points: int = 8,
    initial_fp: int = 10,
) -> tuple[Campaign, PlayService]:
    cid, original = await source_fixture(path / "source", backend, action=kind, fatigue_cost=2)
    base = original._load((await original.store.history(cid))[0].state_after)
    engine = ActionEngine(
        original.engine.reviewer,
        original.engine.resources,
        original.engine.rules.model_copy(
            update={
                "checks": tuple(
                    r.model_copy(update={"modifier": target - 12})
                    for r in original.engine.rules.checks
                )
            }
        ),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{1:064x}")
    initial = campaign(engine)
    actors = tuple(
        ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in base.actors
    )
    actors = tuple(
        a.model_copy(
            update={
                "proposal": a.proposal.model_copy(
                    update={
                        "draft": a.proposal.draft.model_copy(
                            update={
                                "purchases": tuple(
                                    p.model_copy(update={"amount": worker_points})
                                    if p.definition_id == "skill:carpentry"
                                    else p
                                    for p in a.proposal.draft.purchases
                                )
                            }
                        )
                    }
                )
            }
        )
        if a.actor_id == "a"
        else a
        for a in actors
    )
    if low_supervisor:
        actors = tuple(
            a.model_copy(
                update={
                    "proposal": a.proposal.model_copy(
                        update={
                            "draft": a.proposal.draft.model_copy(
                                update={
                                    "purchases": tuple(
                                        p.model_copy(
                                            update={
                                                "amount": 6
                                                if p.definition_id == "attribute:iq"
                                                else 1
                                            }
                                        )
                                        if p.definition_id
                                        in {"attribute:iq", "skill:administration"}
                                        else p
                                        for p in a.proposal.draft.purchases
                                    )
                                }
                            )
                        }
                    )
                }
            )
            if a.actor_id == "b"
            else a
            for a in actors
        )
    genesis = play.initial_state(
        initial,
        base.world,
        base.resources.model_copy(
            update={
                "pools": tuple(
                    p.model_copy(update={"current": initial_fp}) if p.id == "fp:a" else p
                    for p in base.resources.pools
                )
            }
        ),
        actors,
    )
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["inspect", "social"])
@pytest.mark.parametrize("mode", ["ordinary", "gm-secret", "unrolled-secret"])
@pytest.mark.parametrize("target", [2, 3])
async def test_initial_floor_precedes_cost_time_and_original(
    tmp_path: Path, backend: str, kind: Kind, mode: Mode, target: int
) -> None:
    initial, play = await fixture(tmp_path, backend, target=target, kind=kind)
    cid = initial["id"]
    command = (
        PrepareSecretTaskCheck(
            id="begin", actor_id="a", expected_revision=0, check_id="carpentry-inspect"
        )
        if mode == "unrolled-secret"
        else BeginTaskCheck(
            id="begin",
            actor_id="a",
            expected_revision=0,
            check_id="carpentry-inspect",
            secret=mode == "gm-secret",
        )
    )
    principal = "a" if mode == "ordinary" else "gm"
    before = await play.store.read(cid)
    dice = RecordedDice(()) if target == 2 or mode == "unrolled-secret" else RecordedDice((1, 1, 1))
    play.rng = dice
    if target == 2:
        with pytest.raises(ValidationError, match="effective skill must be at least 3"):
            await TaskService(play).execute(cid, command, principal_id=principal)
        assert (
            await play.store.read(cid) == before
            and await play.store.command_input(cid, command.id) is None
        )
        assert snapshot(play._load(before)).pending is None
        assert dice.exhausted()
        return
    begun = await TaskService(play).execute(cid, command, principal_id=principal)
    state = play._load(await play.store.read(cid))
    assert (
        state.resources.game_time == 1
        and next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    )
    pending = snapshot(state).pending
    assert begun.pending_id is not None
    choice: ChooseSecretTaskCheck | ChooseTaskCheck
    if mode == "unrolled-secret":
        assert isinstance(pending, SecretTaskPending) and pending.minimum_skill_generation == 1
        assert snapshot(state).luck.rolls[-1].original is None
        play.rng = RecordedDice((1, 1, 1))
        choice = ChooseSecretTaskCheck(
            id="choose",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            choice="resolve",
        )
    else:
        assert (
            isinstance(pending, TaskPending)
            and pending.original.effective_target == pending.original.total == 3
        )
        assert dice.exhausted()
        play.rng = RecordedDice(())
        choice = ChooseTaskCheck(
            id="choose",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            kind="accept-check",
        )
    result = await TaskService(play).execute(cid, choice, principal_id=principal)
    assert result.check is not None and result.check.effective_target == result.check.total == 3
    assert result.action is not None and result.action.revealed_fact_ids == (
        ("clue",) if kind == "inspect" else ("promise",)
    )
    after = play._load(await play.store.read(cid))
    assert (
        after.resources.game_time == 1
        and next(p.current for p in after.resources.pools if p.id == "fp:a") == 8
    )
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await TaskService(restarted).execute(cid, command, principal_id=principal) == begun
    assert await TaskService(restarted).execute(cid, choice, principal_id=principal) == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mode", ["ordinary", "unrolled-secret"])
@pytest.mark.parametrize("current", [False, True])
async def test_prepared_choices_retain_the_producer_floor_on_replay(
    tmp_path: Path, backend: str, mode: Mode, current: bool
) -> None:
    initial, play = await fixture(tmp_path, backend, target=3 if current else 2)
    cid = initial["id"]
    command = (
        PrepareSecretTaskCheck(
            id="begin", actor_id="a", expected_revision=0, check_id="carpentry-inspect"
        )
        if mode == "unrolled-secret"
        else BeginTaskCheck(
            id="begin", actor_id="a", expected_revision=0, check_id="carpentry-inspect"
        )
    )
    principal = "gm" if mode == "unrolled-secret" else "a"
    if current:
        begun = await TaskService(play).execute(cid, command, principal_id=principal)
    else:
        encoded = json.dumps(
            {
                "operation": "task-host",
                "principal_id": principal,
                "command": command.model_dump(mode="json"),
                "skill_prerequisite_context_generation": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        recorded = CommandRecord(
            campaign_id=cid,
            command_id=command.id,
            actor_id=principal,
            expected_revision=0,
            resulting_revision=1,
            payload_hash=payload_digest({"input": encoded}),
            rules_version="1",
            event={"action": "resource", "outcome": "historical envelope diagnostic"},
            state_after=initial,
            entropy_seed=f"{1:064x}",
            rng_algorithm=RNG_ALGORITHM,
            recorded_at_us=1_700_000_000_000_000,
            command_input=encoded,
        )
        with replay_inputs(recorded):
            begun = await TaskService(play).execute(cid, command, principal_id=principal)
    state = play._load(await play.store.read(cid))
    assert begun.pending_id is not None
    pending = snapshot(state).pending
    choice: ChooseSecretTaskCheck | ChooseTaskCheck
    if mode == "unrolled-secret":
        assert isinstance(pending, SecretTaskPending) and pending.minimum_skill_generation == (
            1 if current else None
        )
        assert ("minimum_skill_generation" in pending.model_dump()) == current
        choice = ChooseSecretTaskCheck(
            id="choose",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            choice="resolve",
        )
    else:
        choice = ChooseTaskCheck(
            id="choose",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            kind="accept-check",
        )
    result = await TaskService(play).execute(cid, choice, principal_id=principal)
    assert (
        result.check is not None
        and result.check.effective_target == (3 if current else 2)
        and result.check.total == 9
    )
    assert result.action is not None and not result.action.revealed_fact_ids
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mode", ["ordinary", "unrolled-secret"])
@pytest.mark.parametrize("final_target", [2, 3])
async def test_actual_drowsy_producer_is_included_in_staged_admission(
    tmp_path: Path, backend: str, mode: Mode, final_target: int
) -> None:
    initial, play = await fixture(tmp_path, backend, target=final_target + 2)
    cid = initial["id"]
    play.seeds = lambda: next(
        f"{i:064x}" for i in range(1, 50000) if sum(draw_dice(SeededRandom(f"{i:064x}"))) == 12
    )
    await produce_drowsy(play, cid)
    before = await play.store.read(cid)
    command = (
        PrepareSecretTaskCheck(
            id="drowsy-check", actor_id="a", expected_revision=2, check_id="carpentry-inspect"
        )
        if mode == "unrolled-secret"
        else BeginTaskCheck(
            id="drowsy-check", actor_id="a", expected_revision=2, check_id="carpentry-inspect"
        )
    )
    principal = "gm" if mode == "unrolled-secret" else "a"
    play.seeds = lambda: f"{1:064x}"
    if final_target == 2:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="effective skill must be at least 3"):
            await TaskService(play).execute(cid, command, principal_id=principal)
        assert await play.store.read(cid) == before and play.rng.exhausted()
        return
    result = await TaskService(play).execute(cid, command, principal_id=principal)
    pending = snapshot(play._load(await play.store.read(cid))).pending
    if mode == "unrolled-secret":
        assert isinstance(pending, SecretTaskPending)
        prepared = TypeAdapter(TaskCheckPreparation).validate_json(pending.preparation_json)
        assert prepared.target + sum(m.value for m in prepared.modifiers) == 3
    else:
        assert isinstance(pending, TaskPending) and pending.original.effective_target == 3
        assert any(
            "drows" in m.reason.lower() and m.value == -2 for m in pending.original.modifiers
        )
    assert result.pending_id == pending.id
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_illegal_intake_precedes_zero_fp_mandatory_exertion(
    tmp_path: Path, backend: str
) -> None:
    # Zero FP is an authored initial condition; no runtime patch fabricates fatigue.
    initial, play = await fixture(tmp_path, backend, target=2, initial_fp=0)
    cid = initial["id"]
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="effective skill must be at least 3"):
        await TaskService(play).execute(
            cid,
            BeginTaskCheck(
                id="illegal", actor_id="a", expected_revision=0, check_id="carpentry-inspect"
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
