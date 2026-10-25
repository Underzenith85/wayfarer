"""Deferred checks retain their producer's approved skill prerequisite facts."""

import asyncio
import json
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from test_enchanting_noncombat_nighttime import configured as magic_fixture
from test_enchanting_noncombat_nighttime import start_choice
from test_live_skill_prerequisite_context import background_fixture, reexecute, revision

from wayfarer.engine.character.skills import SkillError
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActionResult, ActorSetup, Inspect, Social, Wait
from wayfarer.engine.simulation.campaign.activities import PREFIX as ACTIVITY_PREFIX
from wayfarer.engine.simulation.campaign.activities import LongTaskRule
from wayfarer.engine.simulation.campaign.adjudication import Ruling
from wayfarer.engine.simulation.campaign.skill_context_state import PREFIX, enabled
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import default_knowledge_generations as knowledge_generations
from wayfarer.orchestration.adjudication import (
    AdjudicationService,
    DecideRuling,
    ExecuteRuling,
    RequestRuling,
)
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.noncombat import NoncombatCommand
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.task_records import (
    BeginTaskWork,
    BindLongTask,
    ChooseTaskCheck,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.views import campaign_view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["inspect", "noncombat"])
@pytest.mark.parametrize("current", [False, True])
async def test_queued_mage_check_uses_its_own_generation_when_another_group_flushes(
    tmp_path: Path, backend: str, family: Literal["inspect", "noncombat"], current: bool
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path,
        backend,
        enroll=False,
        retain_source_spells=True,
        grouped=True,
        equipment_modifier=1,
        check_modifier=-2,
    )
    cid = initial["id"]
    service = PartyService(play)
    await service.execute(
        cid,
        PartyCommand(
            id="split-mage",
            kind="split_party",
            actor_id="a",
            expected_revision=0,
            target_id="scouts",
        ),
        principal_id="a",
    )
    inner: Inspect | NoncombatCommand
    if family == "noncombat":
        inner = await start_choice(play, cid)
    else:
        inner = Inspect(
            id="observe",
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="forge",
        )
    queue = PartyCommand(
        id="queued-observation",
        kind="queue_activity",
        actor_id="a",
        expected_revision=await revision(play, cid),
        activity_json=inner.model_dump_json(),
    )
    if current:
        accepted = await service.execute(cid, queue, principal_id="a")
    else:
        accepted = await submit(play, cid, service.plan(queue), principal_id="a")
    assert accepted.resources.game_time == 0 and len(accepted.party.queue) == 1
    assert enabled(accepted.resources, "queued-activity", queue.id, "a") == current
    public = json.dumps(campaign_view(accepted, member_for(accepted, "a")))
    assert PREFIX not in public and "skill_prerequisite_context_generation" not in public
    assert "night-clue" not in {f.id for f in accepted.world.perspective("a").facts}
    catch_up = PartyCommand(
        id="catch-up",
        kind="queue_activity",
        actor_id="b",
        expected_revision=await revision(play, cid),
        activity_json=Wait(
            id="wait", actor_id="b", expected_revision=await revision(play, cid), ticks=10
        ).model_dump_json(),
    )
    finished = await service.execute(cid, catch_up, principal_id="b")
    receipt = next(r for r in finished.party.receipts if r.id == queue.id)
    assert receipt.status == ("committed" if current else "rejected")
    assert finished.resources.game_time == 10 and finished.party.queue == ()
    assert ("night-clue" in {f.id for f in finished.world.perspective("a").facts}) == current
    assert next(p.current for p in finished.resources.pools if p.id == "fp:a") == (
        9 if current and family == "noncombat" else 10
    )
    if current and family == "inspect":
        assert finished.last_result is not None and finished.last_result.check is not None
        assert finished.last_result.check.effective_target == 16
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store)
    assert await PartyService(restarted).execute(cid, queue, principal_id="a") == accepted
    assert await PartyService(restarted).execute(cid, catch_up, principal_id="b") == finished
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_background_grouped_social_uses_actual_admitted_skill(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await background_fixture(tmp_path, backend, trained=True, deferred=True)
    cid = initial["id"]
    state = await PartyService(play).execute(
        cid,
        PartyCommand(
            id="background-social",
            kind="queue_activity",
            actor_id="a",
            expected_revision=await revision(play, cid),
            activity_json=Social(
                id="appeal",
                actor_id="a",
                expected_revision=await revision(play, cid),
                target_id="b",
            ).model_dump_json(),
        ),
        principal_id="a",
    )
    assert state.party.receipts[-1].status == "committed"
    assert state.last_result is not None and state.last_result.check is not None
    assert state.last_result.check.effective_target == state.last_result.check.total == 9
    assert state.last_result.revealed_fact_ids == ("promise",)
    assert state.resources.game_time == 3
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("automatic", [False, True])
async def test_paid_background_ruling_retains_approved_context_and_selected_modifier(
    tmp_path: Path, backend: str, automatic: bool
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=True, deferred=True, automatic_ruling=automatic
    )
    cid = initial["id"]
    service = AdjudicationService(play)
    current = await revision(play, cid)
    request = RequestRuling(
        id="background-ruling",
        actor_id="a",
        expected_revision=current,
        action=Social(
            id="appeal",
            actor_id="a",
            expected_revision=current,
            target_id="b",
            approach="intimidation",
        ),
    )
    pending = await service.submit(cid, request, principal_id="a")
    assert isinstance(pending, Ruling) and pending.status == "pending"
    approved: Ruling | ActionResult
    if automatic:
        approved = await service.evaluate(
            cid,
            request.id,
            command_id="approve-ruling",
            expected_revision=await revision(play, cid),
        )
    else:
        approved = await service.submit(
            cid,
            DecideRuling(
                id="approve-ruling",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                ruling_id=request.id,
                approve=True,
                alternative_id="measured",
                reason="Approve the authored measured appeal",
            ),
            principal_id="gm",
        )
    assert isinstance(approved, Ruling) and approved.status == "approved"
    execute = ExecuteRuling(
        id="execute-ruling",
        actor_id="a",
        expected_revision=await revision(play, cid),
        ruling_id=request.id,
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized|affected"):
        await service.submit(cid, execute, principal_id="b")
    with pytest.raises(ConflictError):
        await service.submit(
            cid, execute.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    result, duplicate = await asyncio.gather(
        *(service.submit(cid, execute, principal_id="a") for _ in range(2))
    )
    assert result == duplicate and isinstance(result, ActionResult) and result.check is not None
    assert result.check.base_target == 10 and result.check.effective_target == 11
    assert [m.value for m in result.check.modifiers] == [-1, 2]
    assert result.check.total == 9 and result.revealed_fact_ids == ("promise",)
    state = play._load(await play.store.read(cid))
    assert state.rulings[-1].status == "executed" and state.resources.game_time == 3
    assert "skill_prerequisite_context_generation" not in json.dumps(
        campaign_view(state, member_for(state, "a"))
    )
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store)
    assert await AdjudicationService(restarted).submit(cid, execute, principal_id="a") == result
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("knowledge_version", [0, 1])
async def test_historical_approved_ruling_does_not_adopt_fresh_execution_context(
    tmp_path: Path, backend: str, knowledge_version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial, play, _ = await background_fixture(tmp_path, backend, trained=True, deferred=True)
    cid = initial["id"]
    service = AdjudicationService(play)
    current = await revision(play, cid)
    request = RequestRuling(
        id="legacy-ruling",
        actor_id="a",
        expected_revision=current,
        action=Social(
            id="appeal",
            actor_id="a",
            expected_revision=current,
            target_id="b",
            approach="intimidation",
        ),
    )
    monkeypatch.setattr(knowledge_generations, "CURRENT_VERSION", knowledge_version)
    first = await submit(play, cid, service.plan(request), principal_id="a")
    monkeypatch.setattr(knowledge_generations, "CURRENT_VERSION", 2)
    await service.submit(
        cid,
        DecideRuling(
            id="approve-legacy",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
            approve=True,
            alternative_id="measured",
            reason="Approve the original recorded alternative",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    assert not enabled(play._load(before).resources, "ruling", request.id, "a")
    with pytest.raises(SkillError, match="prerequisite"):
        await service.submit(
            cid,
            ExecuteRuling(
                id="execute-legacy",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ruling_id=request.id,
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    assert await service.submit(cid, request, principal_id="a") == first
    await reexecute(initial, play, tmp_path / "reexecute")


async def test_private_context_marker_cannot_enter_authored_genesis(tmp_path: Path) -> None:
    initial, play, genesis = await magic_fixture(tmp_path, enroll=False, retain_source_spells=True)
    with pytest.raises(ValidationError, match="cannot seed"):
        play.initial_state(
            initial,
            genesis.world,
            ResourceState(
                events=(ResourceEvent(id=PREFIX + "forged", at=0, target_id="a", kind="{}"),)
            ),
            tuple(
                ActorSetup.model_validate(a.model_dump(exclude={"approval"}))
                for a in genesis.actors
            ),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wait_after_selected_long_work_neither_recomputes_nor_grants_more_labor(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path, backend, enroll=False, retain_source_spells=True
    )
    cid = initial["id"]
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind-work",
            actor_id="a",
            expected_revision=0,
            location_id="forge",
            rule=LongTaskRule(id="watch", target_id="skill:observation", required_man_hours=2),
        ),
        principal_id="gm",
    )
    begun = await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id="work",
            actor_id="a",
            expected_revision=await revision(play, cid),
            task_id="watch",
            seconds=3600,
        ),
        principal_id="a",
    )
    assert begun.pending_id is not None
    selected = await TaskService(play).execute(
        cid,
        ChooseTaskCheck(
            id="accept-work",
            actor_id="a",
            expected_revision=await revision(play, cid),
            pending_id=begun.pending_id,
            kind="accept-check",
        ),
        principal_id="a",
    )
    assert selected.activity is not None and selected.activity.progress == 1
    before = play._load(await play.store.read(cid))
    events = tuple(e for e in before.resources.events if e.id.startswith(ACTIVITY_PREFIX))
    assert snapshot(before).pending is None
    dice = RecordedDice(())
    play.rng = dice
    waited = await play.execute(
        cid,
        Wait(id="rest", actor_id="a", expected_revision=before.revision, ticks=100),
        principal_id="a",
    )
    after = play._load(await play.store.read(cid))
    assert waited.status == "committed" and after.resources.game_time == 3700
    assert dice.exhausted()
    assert tuple(e for e in after.resources.events if e.id.startswith(ACTIVITY_PREFIX)) == events
