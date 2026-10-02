"""Actual social and scheduler callers enroll replayable bounded reactions."""

import json
from pathlib import Path
from typing import Literal

import pytest
from test_reaction_task_host import choose, fixture, source
from test_secret_task_host import private_result

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.campaign.npcs import (
    NPCAction,
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ConflictError
from wayfarer.orchestration.npcs import NPCService
from wayfarer.orchestration.reaction_records import ChooseReaction
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_social_service_enters_pending_route_and_cannot_reopen_committed_trigger(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    authored = source()
    service = SocialService(
        play,
        lambda play, state, command: ResolvedInteraction(
            SocialContext("gurps-basic-set-4e-2004", 0), authored.disclosure
        ),
    )
    state = play._load(await play.store.read(cid))
    command = SocialCommand(
        id="social-service",
        actor_id="a",
        subject_id="npc",
        trigger_id=authored.trigger_id,
        kind="reaction",
        expected_revision=state.revision,
    )
    opened = await service.prepare_reaction(cid, command, authored, principal_id="gm")
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4))
    await choose(play, cid, opened.pending_id)
    state = play._load(await play.store.read(cid))
    assert ("a", "answer") in state.world.knowledge
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await service.prepare_reaction(
            cid,
            command.model_copy(update={"id": "reopen", "expected_revision": state.revision}),
            authored,
            principal_id="gm",
        )
    ordinary_source = source("ordinary")
    ordinary = command.model_copy(
        update={"id": "ordinary", "trigger_id": "ordinary", "expected_revision": state.revision}
    )
    play.rng = RecordedDice((3, 3, 3))
    await service.execute(cid, ordinary, principal_id="gm")
    play.rng = RecordedDice(())
    state = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError, match="already resolved"):
        await service.prepare_reaction(
            cid,
            ordinary.model_copy(update={"id": "too-late", "expected_revision": state.revision}),
            ordinary_source,
            principal_id="gm",
        )


def rules(*, earlier: bool = False) -> NPCSocialRules:
    reaction = NPCSocialPlan(
        id="second",
        actor_id="a",
        goal="Ask the guard for directions",
        first_due=2,
        interval=10,
        action_budget=1,
        actions=(
            NPCSocialAction(
                id="ask",
                kind="communicate",
                social=NPCSocialTrigger(
                    kind="reaction", subject_id="npc", disclosure_fact_ids=("answer",)
                ),
            ),
        ),
    )
    first = NPCSocialPlan(
        id="first",
        actor_id="b",
        goal="Wait at the dock",
        first_due=1 if earlier else 2,
        interval=10,
        action_budget=1,
        actions=(NPCAction(id="patrol", kind="patrol"),),
    )
    return NPCSocialRules(id="reaction-plans", plans=(reaction, first))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("earlier", [True, False])
async def test_scheduler_stops_after_prior_work_before_selected_reaction(
    tmp_path: Path, backend: str, earlier: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, npc_rules=rules(earlier=earlier))
    state = play._load(await play.store.read(cid))
    command = NPCService(play).prepare_reaction_command(
        state,
        command_id="npc-prepare",
        actor_id="a",
        plan_id="second",
        action_id="ask",
        active_interaction=True,
        sapient=True,
    )
    before = await play.store.read(cid)
    if earlier:
        with pytest.raises(ConflictError, match="chronological"):
            await TaskService(play).execute(cid, command, principal_id="gm")
        assert await play.store.read(cid) == before
        await play.execute(
            cid,
            Wait(id="earlier-patrol", actor_id="b", expected_revision=state.revision, ticks=1),
            principal_id="b",
        )
        state = play._load(await play.store.read(cid))
        assert (
            next(
                progress for progress in state.npcs.progress if progress.plan_id == "first"
            ).spent_actions
            == 1
        )
    command = command.model_copy(update={"expected_revision": state.revision})
    opened = await TaskService(play).execute(cid, command, principal_id="gm")
    prepared = play._load(await play.store.read(cid))
    assert prepared.resources.game_time == 2 and ("a", "answer") not in prepared.world.knowledge
    assert (
        next(
            progress for progress in prepared.npcs.progress if progress.plan_id == "second"
        ).spent_actions
        == 0
    )
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4))
    chosen, result = await choose(play, cid, opened.pending_id)
    state = play._load(await play.store.read(cid))
    assert state.world.knowledge.count(("a", "answer")) == 1
    assert [
        (progress.plan_id, progress.spent_actions, progress.clock)
        for progress in state.npcs.progress
    ] == [("second", 1, 1), ("first", 1, 1)]
    assert (
        next(decision for decision in state.npcs.decisions if decision.plan_id == "second").status
        == "committed"
    )
    play.rng = RecordedDice(())
    assert await TaskService(play).execute(cid, chosen, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert snapshot(state).pending is None
    assert json.loads(private_result(state).reaction_json or "{}")["reaction"]["total"] == 15


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mode", ["influence", "skill"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_diplomacy_freezes_preceding_contest_and_selects_only_fallback(
    tmp_path: Path,
    backend: str,
    mode: str,
    choice: Literal["use-luck", "resolve", "cancel"],
) -> None:
    from test_reaction_task_host import prepare

    from wayfarer.engine.simulation.social.diplomacy import PreparedDiplomacy
    from wayfarer.orchestration.reaction_records import (
        SecretReactionPending,
    )

    cid, play = await fixture(tmp_path, backend)
    authored = source().model_copy(
        update={
            "mode": mode,
            "npc_will": 12,
            "conditions": ("audience-audible", "shared-language") if mode == "skill" else (),
        }
    )
    contest_dice = RecordedDice((6, 6, 6, 3, 3, 3))
    play.rng = contest_dice
    _, opened = await prepare(play, cid, authored=authored)
    assert contest_dice.exhausted()
    before = play._load(await play.store.read(cid))
    pending = snapshot(before).pending
    assert isinstance(pending, SecretReactionPending)
    frozen = PreparedDiplomacy.model_validate_json(pending.preparation_json)
    assert frozen.influence.outcome == "bad" and frozen.influence.fallback is None
    assert ("a", "answer") not in before.world.knowledge
    faces = (
        (3, 3, 3, 5, 5, 5, 4, 4, 4)
        if choice == "use-luck"
        else (3, 3, 3)
        if choice == "resolve"
        else ()
    )
    target_dice = RecordedDice(faces)
    play.rng = target_dice
    command = ChooseReaction(
        id="declare",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=opened.pending_id or "",
        choice=choice,
    )
    await TaskService(play).execute(
        cid, command, principal_id="a" if choice == "use-luck" else "gm"
    )
    assert target_dice.exhausted()
    after = play._load(await play.store.read(cid))
    assert (("a", "answer") in after.world.knowledge) is (choice == "use-luck")
    assert pending.preparation_json == frozen.model_dump_json()
    if choice != "cancel":
        event = next(
            event
            for event in after.resources.events
            if event.id.startswith(("social:", "social-key:"))
        )
        details = json.loads(event.kind)["private"]
        influence = details if mode == "influence" else details["influence"]
        assert influence["contest"]["first"]["dice"] == [6, 6, 6]
        assert influence["contest"]["second"]["dice"] == [3, 3, 3]
        assert influence["fallback"]["total"] == (15 if choice == "use-luck" else 9)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authored_npc_skill_diplomacy_applies_selected_fact_and_occurrence_once(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.social.diplomacy import PreparedDiplomacy
    from wayfarer.orchestration.reaction_records import SecretReactionPending

    authored = NPCSocialRules(
        id="npc-diplomacy",
        plans=(
            NPCSocialPlan(
                id="parley",
                actor_id="a",
                goal="Ask the guard about the route",
                first_due=2,
                interval=10,
                action_budget=1,
                actions=(
                    NPCSocialAction(
                        id="ask",
                        kind="communicate",
                        social=NPCSocialTrigger(
                            kind="skill",
                            subject_id="npc",
                            skill_id="skill:diplomacy",
                            npc_will=12,
                            conditions=("audience-audible", "shared-language"),
                            disclosure_fact_ids=("answer",),
                        ),
                    ),
                ),
            ),
        ),
    )
    cid, play = await fixture(tmp_path, backend, npc_rules=authored)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((6, 6, 6, 3, 3, 3))
    command = NPCService(play).prepare_reaction_command(
        before,
        command_id="npc-parley",
        actor_id="a",
        plan_id="parley",
        action_id="ask",
        active_interaction=True,
        sapient=True,
    )
    opened = await TaskService(play).execute(cid, command, principal_id="gm")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, SecretReactionPending)
    prepared = PreparedDiplomacy.model_validate_json(pending.preparation_json)
    assert prepared.influence.outcome == "bad"
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4))
    terminal, result = await choose(play, cid, opened.pending_id)
    saved = await play.store.read(cid)
    final = play._load(saved)
    assert final.world.knowledge.count(("a", "answer")) == 1
    assert (final.npcs.progress[0].spent_actions, final.npcs.progress[0].clock) == (1, 1)
    assert final.npcs.decisions[0].status == "committed" and final.resources.game_time == 2
    social_event = next(
        event for event in final.resources.events if event.id.startswith(("social:", "social-key:"))
    )
    trace = json.loads(social_event.kind)
    assert json.loads(trace["public"])["outcome"] == "good"
    assert trace["private"]["influence"]["contest"]["first"]["dice"] == [6, 6, 6]
    play.rng = RecordedDice(())
    assert await TaskService(play).execute(cid, terminal, principal_id="a") == result
    assert await play.store.read(cid) == saved == await play.store.replay(cid)


async def test_unknown_disclosure_facts_and_outcomes_refuse_before_target_dice(
    tmp_path: Path,
) -> None:
    from test_reaction_task_host import prepare

    from wayfarer.engine.simulation.social.social import SocialDisclosure
    from wayfarer.errors import ValidationError

    cid, play = await fixture(tmp_path)
    before = await play.store.read(cid)
    for disclosure in (SocialDisclosure(("unseen",)), SocialDisclosure(("answer",), ("invented",))):
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError):
            await prepare(
                play, cid, authored=source().model_copy(update={"disclosure": disclosure})
            )
        assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("route", ["influence", "skill", "npc"])
async def test_diplomacy_preserves_balance_guard_before_all_prerequisite_entropy(
    tmp_path: Path, backend: str, route: str
) -> None:
    from test_combat_sensory_authority import change
    from test_innate_criticals import context, fight, miss, table
    from test_reaction_task_host import prepare

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.traits.innate_criticals import resolve_innate_miss
    from wayfarer.errors import ValidationError

    authored = (
        NPCSocialRules(
            id="balance",
            plans=(
                NPCSocialPlan(
                    id="parley",
                    actor_id="a",
                    goal="Ask",
                    first_due=1,
                    interval=2,
                    action_budget=1,
                    actions=(
                        NPCSocialAction(
                            id="ask",
                            kind="communicate",
                            social=NPCSocialTrigger(
                                kind="skill",
                                subject_id="npc",
                                skill_id="skill:diplomacy",
                                conditions=("audience-audible", "shared-language"),
                            ),
                        ),
                    ),
                ),
            ),
        )
        if route == "npc"
        else None
    )
    cid, play = await fixture(tmp_path, backend, npc_rules=authored)

    def lose_balance(state: PlayState) -> PlayState:
        resources, _, outcome = resolve_innate_miss(
            state.resources,
            fight(),
            context().model_copy(update={"campaign_id": cid}),
            miss(),
            rng=RecordedDice(table(7)),
            system=True,
        )
        assert outcome.effect == "lose-balance"
        return state.model_copy(update={"resources": resources})

    await change(play, cid, lose_balance)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="even free actions"):
        if route == "npc":
            state = play._load(before)
            command = NPCService(play).prepare_reaction_command(
                state,
                command_id="blocked",
                actor_id="a",
                plan_id="parley",
                action_id="ask",
                active_interaction=True,
                sapient=True,
            )
            await TaskService(play).execute(cid, command, principal_id="gm")
        else:
            await prepare(
                play,
                cid,
                authored=source().model_copy(
                    update={
                        "mode": route,
                        "conditions": ("audience-audible", "shared-language")
                        if route == "skill"
                        else (),
                    }
                ),
            )
    assert await play.store.read(cid) == before
    if route != "npc":
        # The NPC can still react to someone who cannot voluntarily take an action.
        _, opened = await prepare(
            play,
            cid,
            "passive",
            authored=source("passive").model_copy(update={"active_interaction": False}),
        )
        play.rng = RecordedDice((5, 5, 5))
        await choose(play, cid, opened.pending_id, "resolve")
        assert ("a", "answer") in play._load(await play.store.read(cid)).world.knowledge


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_tied_predecessors_preserve_finite_budgets_without_consuming_target(
    tmp_path: Path, backend: str
) -> None:
    base = rules(earlier=False)
    first = base.plans[1]
    middle = first.model_copy(update={"id": "middle"})
    authored = base.model_copy(
        update={"plans": (base.plans[0], first, middle), "checkpoint_budget": 3}
    )
    cid, play = await fixture(tmp_path, backend, npc_rules=authored)
    state = play._load(await play.store.read(cid))
    command = NPCService(play).prepare_reaction_command(
        state,
        command_id="prior-one",
        actor_id="a",
        plan_id="second",
        action_id="ask",
        active_interaction=True,
        sapient=True,
    )
    play.rng = RecordedDice(())
    opened = await TaskService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert opened.status == "pending"
    assert [(item.plan_id, item.spent_actions) for item in state.npcs.progress] == [
        ("second", 0),
        ("first", 1),
        ("middle", 1),
    ]
    play.rng = RecordedDice((5, 5, 5))
    await choose(play, cid, opened.pending_id, "resolve")
    state = play._load(await play.store.read(cid))
    assert [(item.plan_id, item.spent_actions) for item in state.npcs.progress] == [
        ("second", 1),
        ("first", 1),
        ("middle", 1),
    ]
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_tied_npc_pending_boundary_seed_reexecution(
    tmp_path: Path, backend: str, choice: Literal["use-luck", "resolve", "cancel"]
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await fixture(tmp_path, backend, npc_rules=rules(earlier=False))
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    command = NPCService(play).prepare_reaction_command(
        play._load(initial),
        command_id="tied",
        actor_id="a",
        plan_id="second",
        action_id="ask",
        active_interaction=True,
        sapient=True,
    )
    opened = await TaskService(play).execute(cid, command, principal_id="gm")
    prepared = play._load(await play.store.read(cid))
    assert [(item.plan_id, item.spent_actions) for item in prepared.npcs.progress] == [
        ("second", 0),
        ("first", 1),
    ]
    assert ("a", "answer") not in prepared.world.knowledge
    await choose(play, cid, opened.pending_id, choice)
    final = await play.store.read(cid)
    state = play._load(final)
    assert (("a", "answer") in state.world.knowledge) is (choice != "cancel")
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=prepared.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
