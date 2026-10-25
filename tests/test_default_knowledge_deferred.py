"""B173 accepted deferred producers retain their initiating adoption policy."""

import secrets
from pathlib import Path

import pytest
from test_default_knowledge_hosts import adopt
from test_live_skill_prerequisite_context import background_fixture, reexecute, revision

from wayfarer.engine.simulation.actions import ActionResult, Inspect, Social, Wait
from wayfarer.engine.simulation.campaign.adjudication import Ruling
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.engine.simulation.campaign.default_knowledge_producers import enabled
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.adjudication import (
    AdjudicationService,
    DecideRuling,
    ExecuteRuling,
    RequestRuling,
)
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.party import PartyCommand, PartyService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version,adopted_at_queue", [(1, False), (1, True), (2, True)])
async def test_actual_queued_default_pins_origin_policy_before_later_gm_knowledge_change(
    tmp_path: Path,
    backend: str,
    version: int,
    adopted_at_queue: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Generation 1 really accepted unadopted origins; generation 2 needs facts.
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, deferred=True, authored_modifier=0
    )
    cid = initial["id"]
    party = PartyService(play)
    await party.execute(
        cid,
        PartyCommand(
            id="split",
            kind="split_party",
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="scouts",
        ),
        principal_id="a",
    )
    if adopted_at_queue:
        await adopt(play, cid, ("skill:observation",))
    current = await revision(play, cid)
    await party.execute(
        cid,
        PartyCommand(
            id="queued-observe",
            kind="queue_activity",
            actor_id="a",
            expected_revision=current,
            activity_json=Inspect(
                id="observe", actor_id="a", expected_revision=current, target_id="chest"
            ).model_dump_json(),
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert enabled(state.resources, "queued-activity", "queued-observe", "a") == adopted_at_queue
    if adopted_at_queue:
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="withdraw",
                actor_id="gm",
                expected_revision=state.revision,
                performer_id="a",
                skill_ids=("skill:observation",),
                society_known=False,
                reason="The GM corrects societal knowledge before the queued original",
            ),
            principal_id="gm",
        )
    else:
        await adopt(play, cid)
    play.rng = secrets
    play.seeds = lambda: f"{11:064x}"
    current = await revision(play, cid)
    result = await party.execute(
        cid,
        PartyCommand(
            id="catch-up",
            kind="queue_activity",
            actor_id="b",
            expected_revision=current,
            activity_json=Wait(
                id="wait", actor_id="b", expected_revision=current, ticks=10
            ).model_dump_json(),
        ),
        principal_id="b",
    )
    receipt = next(r for r in result.party.receipts if r.id == "queued-observe")
    assert receipt.status == ("rejected" if adopted_at_queue else "committed")
    assert ("clue" in {fact.id for fact in result.world.perspective("a").facts}) == (
        not adopted_at_queue
    )
    assert "queued-observe" not in {activity.id for activity in result.party.queue}
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2])
async def test_actual_ruling_default_uses_request_policy_under_fresh_resolver_flags(
    tmp_path: Path, backend: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, deferred=True, authored_modifier=0
    )
    cid = initial["id"]
    await adopt(play, cid, () if version == 0 else ("skill:diplomacy",))
    monkeypatch.setattr(generations, "ACTIVE", version > 0)
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    service = AdjudicationService(play)
    current = await revision(play, cid)
    request = RequestRuling(
        id="request",
        actor_id="a",
        expected_revision=current,
        action=Social(
            id="social",
            actor_id="a",
            expected_revision=current,
            target_id="b",
            approach="intimidation",
        ),
    )
    pending = await service.submit(cid, request, principal_id="a")
    assert isinstance(pending, Ruling)
    state = play._load(await play.store.read(cid))
    assert enabled(state.resources, "ruling", request.id, "a") == (version > 0)
    monkeypatch.setattr(generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    await service.submit(
        cid,
        DecideRuling(
            id="decide",
            actor_id="gm",
            expected_revision=state.revision,
            ruling_id=request.id,
            approve=True,
            alternative_id="measured",
            reason="Approve this source-bound alternative",
        ),
        principal_id="gm",
    )
    play.rng, play.seeds = secrets, lambda: f"{11:064x}"
    result = await service.submit(
        cid,
        ExecuteRuling(
            id="execute",
            actor_id="a",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
        ),
        principal_id="a",
    )
    assert isinstance(result, ActionResult) and result.check
    assert result.check.base_target == 4 and result.check.effective_target == 6
    assert result.check.total == 5 and result.revealed_fact_ids == ("promise",)
    await reexecute(initial, play, tmp_path / "reexecute")
