"""Actual ordinary checks merge GM society facts with their paid access context."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_runtime
from test_default_knowledge_hosts import adopt
from test_live_skill_prerequisite_context import background_fixture, reexecute, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Inspect, Social
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.errors import ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("social", [False, True])
async def test_actual_public_default_needs_gm_knowledge_even_after_background_payment(
    tmp_path: Path, backend: str, social: bool
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=False, authored_modifier=0
    )
    cid = initial["id"]
    await adopt(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    actor = next(a for a in state.actors if a.actor_id == "a")
    skill = "skill:diplomacy" if social else "skill:observation"
    command = (Social if social else Inspect)(
        id="ordinary",
        actor_id="a",
        expected_revision=state.revision,
        target_id="b" if social else "chest",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="GM-known default"):
        await play.execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    view = await build_runtime(play).read(cid, principal_id="a")
    characters = view["characters"]
    assert isinstance(characters, tuple)
    assert skill not in {v["target"] for v in characters[0]["values"]}
    play.rng = secrets
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="skill-known",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=(skill,),
            society_known=True,
            reason="The GM confirms this concrete ordinary skill is known in the actor's society",
        ),
        principal_id="gm",
    )
    command = command.model_copy(update={"expected_revision": await revision(play, cid)})
    assert (await play.preview(cid, command, principal_id="a")).status == "feasible"
    play.seeds = lambda: f"{11:064x}"
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.base_target == (4 if social else 5)
    assert result.check.total == 5 and result.check.outcome.succeeded == (not social)
    assert result.revealed_fact_ids == (() if social else ("clue",))
    final = play._load(await play.store.read(cid))
    assert next(a for a in final.actors if a.actor_id == "a") == actor
    await reexecute(initial, play, tmp_path / "reexecute")
