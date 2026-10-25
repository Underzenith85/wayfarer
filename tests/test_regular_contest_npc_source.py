"""B349 source semantics cover pending NPC decisions and task-clock checkpoints."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_social_dispatch import prepare as social_prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY
from wayfarer.engine.rules.skills.mundane.social.inventory import definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, CheckRule, Wait
from wayfarer.engine.simulation.campaign.npcs import (
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import ValidationError
from wayfarer.orchestration import social_generations
from wayfarer.orchestration.npcs import NPCProposal, NPCService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import BeginTaskCheck, ChooseTaskCheck, SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str) -> tuple[str, PlayService, Campaign]:
    rules = NPCSocialRules(
        id="question-plan",
        plans=(
            NPCSocialPlan(
                id="question",
                actor_id="a",
                goal="Question the guard",
                first_due=1,
                interval=1,
                action_budget=1,
                actions=(
                    NPCSocialAction(
                        id="ask",
                        kind="communicate",
                        social=NPCSocialTrigger(
                            kind="skill",
                            subject_id="npc",
                            skill_id="skill:interrogation",
                            npc_will=16,
                            conditions=("subject-restrained", "shared-language"),
                            disclosure_fact_ids=("disclosure",),
                        ),
                    ),
                ),
            ),
        ),
    )
    original_id, original = await social_prepare(path / "source", rules)
    source = original._load(await original.store.read(original_id))
    compiler = original.engine.reviewer.compiler
    definition = next(d for d in definitions() if d.id == "skill:interrogation")
    selected = DEFAULT_REGISTRY.get("profile:gurps-basic-set-4e-2004", 3)
    base = selected.catalog.package(compiler.rules.packages[0])
    package = replace(
        base,
        id=base.id + ":regular-source",
        definitions=tuple(compiler.definitions.values()) + (definition,),
    )
    catalog = RulesCatalog((package,))
    pinned = replace(
        compiler.rules, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    compiler = CharacterCompiler(
        catalog, pinned, compiler.policy, statistics_profile="gurps-basic-set-4e-2004"
    )
    reviewer = PowerReviewer(
        compiler, original.engine.reviewer.policy, original.engine.reviewer.gm_ids
    )
    resources = ResourceEngine(source.world, catalog, pinned, compiler.policy, ())
    runtime = original.engine.rules.model_copy(
        update={
            "checks": (
                CheckRule(
                    id="question-task",
                    action="social",
                    target_id="npc",
                    definition_id="skill:diplomacy",
                    package_id=package.id,
                    package_version=package.version,
                    duration=1,
                    reveal_fact_ids=("disclosure",),
                ),
            )
        }
    )
    engine = ActionEngine(reviewer, resources, runtime)
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: f"{2:064x}"
    )
    initial = campaign(engine)
    actor = source.actors[0]
    proposal = actor.proposal.model_copy(
        update={
            "draft": actor.proposal.draft.model_copy(
                update={
                    "purchases": actor.proposal.draft.purchases
                    + (
                        Purchase(definition_id=definition.id, amount=24),
                        Purchase(definition_id="skill:diplomacy", amount=8),
                    ),
                }
            )
        }
    )
    state = play.initial_state(
        initial,
        source.world,
        source.resources,
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("npc",)),),
        members=source.members,
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play, initial


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("route", ["wait", "task"])
async def test_pending_director_choice_uses_captured_source_generation(
    tmp_path: Path,
    backend: str,
    version: int,
    route: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(social_generations, "CURRENT_VERSION", version)
    cid, play, initial = await prepare(tmp_path, backend)
    if route == "task":
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=0,
                running=True,
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    proposal = NPCProposal(
        id="director-choice",
        actor_id="gm",
        expected_revision=state.revision,
        plan_id="question",
        action_id="ask",
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await NPCService(play).propose(cid, proposal, principal_id="alice")
    assert await play.store.read(cid) == before
    await NPCService(play).propose(cid, proposal, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 0
    assert any(d.status == "proposed" and d.action_id == "ask" for d in state.npcs.decisions)
    command: Wait | BeginTaskCheck
    if route == "wait":
        command = Wait(id="advance", actor_id="a", expected_revision=state.revision, ticks=1)
        await play.execute(cid, command, principal_id="a")
    else:
        command = BeginTaskCheck(
            id="advance",
            actor_id="a",
            expected_revision=state.revision,
            check_id="question-task",
        )
        opened = await TaskService(play).execute(cid, command, principal_id="alice")
        state = play._load(await play.store.read(cid))
        assert opened.pending_id and opened.check
        assert opened.check.effective_target == 11
        assert opened.check.total == (10 if version in (2, 3) else 14 if version < 2 else 10)
        await TaskService(play).execute(
            cid,
            ChooseTaskCheck(
                id="finish-task",
                actor_id="a",
                expected_revision=state.revision,
                pending_id=opened.pending_id,
                kind="accept-check",
            ),
            principal_id="alice",
        )
    final = await play.store.read(cid)
    state = play._load(final)
    event = next(e for e in state.resources.events if e.id.startswith(("social:", "social-key:")))
    public = json.loads(json.loads(event.kind)["public"])
    assert public["outcome"] == (
        "interrogation-resolved" if version >= 4 else "interrogation-answered"
    )
    private = json.loads(event.kind)["private"]
    assert private["base_skill"] == 16
    if version >= 4:
        pair = (private["contest"]["first"], private["contest"]["second"])
        assert private["resolution"] == "quick-contest" and private["rounds"] is None
        assert [c["effective_target"] for c in pair] == [16, 16]
    else:
        pair = private["rounds"]["rounds"][0]
        assert [c["effective_target"] for c in pair] == ([10, 10] if version >= 2 else [14, 14])
        assert (len(private["rounds"]["rounds"]) == 1) == (version >= 2)
    assert private["effect"]["id"] == "interrogation-answered"
    assert (("a", "disclosure") in state.world.knowledge) == (route == "task" and version >= 2)
    assert state.resources.game_time == 1
    assert next(p for p in state.npcs.progress if p.plan_id == "question").spent_actions == 1
    assert any(d.status == "committed" and d.action_id == "ask" for d in state.npcs.decisions)
    monkeypatch.setattr(social_generations, "CURRENT_VERSION", 2 if version < 2 else 1)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    await NPCService(restarted).propose(cid, proposal, principal_id="gm")
    if route == "wait":
        await restarted.execute(cid, command, principal_id="a")
    else:
        await TaskService(restarted).execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == final
