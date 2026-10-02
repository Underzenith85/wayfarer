"""B66 selected secret reactions change actual knowledge through the task host."""

import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_orchestrator, build_play, build_runtime
from test_actions import world
from test_secret_task_host import private_result
from test_task_host import fixture as task_fixture
from test_wave9 import FakeProvider

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import ReactionModifier
from wayfarer.engine.simulation.campaign.npcs import (
    NPCReputation,
    NPCSocialRules,
    NPCSocialStanding,
)
from wayfarer.engine.simulation.social.social import SocialDisclosure
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import (
    AuthoredSocialReaction,
    ChooseReaction,
    PrepareReaction,
    SecretReactionPending,
)
from wayfarer.orchestration.task_records import TaskResult, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands

Choice = Literal["use-luck", "resolve", "cancel"]


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    points: int = 15,
    modifiers: tuple[str, ...] = (),
    npc_rules: NPCSocialRules | None = None,
    social_traits: tuple[Purchase, ...] = (),
) -> tuple[str, PlayService]:
    initial = world()
    authored_world = replace(
        initial,
        entities=initial.entities + (Entity("npc", EntityKind.ACTOR, "Guard", location_id="dock"),),
        facts=initial.facts
        + (Fact("answer", "dock", "route", "north"), Fact("unrelated", "npc", "fear", "fire")),
        knowledge=(("npc", "answer"), ("npc", "unrelated")),
    )
    return await task_fixture(
        path,
        backend,
        points=points,
        modifiers=modifiers,
        action="social",
        npc_rules=npc_rules,
        world_override=authored_world,
        social_traits=social_traits,
    )


def source(
    identifier: str = "audience", modifier: int = 0, *, standing: NPCSocialStanding | None = None
) -> AuthoredSocialReaction:
    return AuthoredSocialReaction(
        active_interaction=True,
        sapient=True,
        trigger_id=identifier,
        subject_id="npc",
        modifiers=(ReactionModifier("situation", modifier, identifier, True),),
        standing=standing,
        required_fact_ids=("answer",),
        disclosure=SocialDisclosure(("answer",)),
    )


async def prepare(
    play: PlayService,
    cid: str,
    identifier: str = "reaction",
    *,
    authored: AuthoredSocialReaction | None = None,
) -> tuple[PrepareReaction, TaskResult]:
    state = play._load(await play.store.read(cid))
    command = PrepareReaction(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        source=authored or source(identifier),
    )
    return command, await TaskService(play).execute(cid, command, principal_id="gm")


async def choose(
    play: PlayService,
    cid: str,
    pending_id: str | None,
    choice: Choice = "use-luck",
    *,
    identifier: str = "declare",
    principal: str | None = None,
) -> tuple[ChooseReaction, TaskResult]:
    assert pending_id is not None
    state = play._load(await play.store.read(cid))
    command = ChooseReaction(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending_id,
        choice=choice,
    )
    return command, await TaskService(play).execute(
        cid, command, principal_id=principal or ("a" if choice == "use-luck" else "gm")
    )


def opaque(result: TaskResult) -> None:
    assert result.secret and result.reaction_json is None
    assert result.check is None and result.luck is None and result.action is None
    assert result.reason == ""


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_good_reaction_discloses_exact_bound_fact_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice(())
    _, opened = await prepare(play, cid)
    before = play._load(await play.store.read(cid))
    assert isinstance(snapshot(before).pending, SecretReactionPending)
    assert snapshot(before).luck.rolls[-1].original is None
    assert before.world.knowledge == (("npc", "answer"), ("npc", "unrelated"))
    pending = await TaskService(play).pending(cid, principal_id="a")
    assert pending and pending.pending_id == opened.pending_id
    opaque(pending)
    dice = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4))
    play.rng = dice
    command, result = await choose(play, cid, opened.pending_id)
    assert dice.exhausted()
    opaque(result)
    saved = await play.store.read(cid)
    after = play._load(saved)
    private = private_result(after)
    assert private.luck and private.luck.chosen_index == 1
    assert private.luck.attempts == ((3, 3, 3), (5, 5, 5), (4, 4, 4))
    assert private.reaction_json
    assert json.loads(private.reaction_json)["reaction"]["total"] == 15
    assert json.loads(private.reaction_json)["reaction"]["outcome"] == "good"
    assert after.world.knowledge == (("a", "answer"), ("npc", "answer"), ("npc", "unrelated"))
    assert snapshot(after).pending is None and len(real_play_clock(after).cooldowns) == 1
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert await restarted.store.read(cid) == saved == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "choice,count,learned", [("resolve", 3, False), ("cancel", 0, False), ("use-luck", 9, True)]
)
async def test_unrolled_entropy_and_ordinary_or_cancel(
    tmp_path: Path, backend: str, choice: Choice, count: int, learned: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid)
    dice = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4)[:count])
    play.rng = dice
    _, result = await choose(play, cid, opened.pending_id, choice)
    state = play._load(await play.store.read(cid))
    assert dice.exhausted() and (("a", "answer") in state.world.knowledge) is learned
    assert result.status == ("cancelled" if choice == "cancel" else "completed")
    assert len(snapshot(state).luck.receipts) == (choice == "use-luck")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_recognition_runs_once_for_all_candidates(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend)
    authored = source(
        standing=NPCSocialStanding(
            reputations=(NPCReputation(id="rep", level=2, recognition="sometimes"),)
        )
    )
    _, opened = await prepare(play, cid, authored=authored)
    dice = RecordedDice((2, 2, 2, 3, 3, 3, 5, 5, 5, 4, 4, 4))
    play.rng = dice
    await choose(play, cid, opened.pending_id)
    assert dice.exhausted()
    result = private_result(play._load(await play.store.read(cid)))
    assert result.reaction_json
    trace = json.loads(result.reaction_json)
    assert trace["reaction"]["total"] == 17 and trace["reaction"]["outcome"] == "very-good"
    assert len(trace["recognition"]) == 1 and trace["recognition"][0]["dice"] == [2, 2, 2]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_seed_only_reexecution_and_secret_projections(
    tmp_path: Path, backend: str, choice: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, opened = await prepare(play, cid)
    runtime = build_runtime(play)
    orchestrator = build_orchestrator(runtime, FakeProvider())
    for stage in ("pending", "completed"):
        if stage == "completed":
            await choose(play, cid, opened.pending_id, choice)
        context, _, _ = await orchestrator.context(cid, "a", "a")
        projection = json.dumps(await runtime.read(cid, principal_id="a"))
        events = json.dumps(
            [event.model_dump(mode="json") for event in await runtime.events(cid, principal_id="a")]
        )
        for secret in (
            '"dice"',
            '"attempts"',
            '"recognition"',
            '"good"',
            '"poor"',
            "task-host:",
            "preparation_json",
        ):
            assert secret not in context and secret not in projection and secret not in events
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "choice,modifier,total,band", [("resolve", 2, 17, "very-good"), ("use-luck", -2, 13, "good")]
)
async def test_reaction_roll_record_retains_the_actual_modifier_and_total(
    tmp_path: Path, backend: str, choice: Choice, modifier: int, total: int, band: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid, authored=source(modifier=modifier))
    play.rng = RecordedDice((3, 3, 3, 5, 5, 5, 4, 4, 4) if choice == "use-luck" else (5, 5, 5))
    await choose(play, cid, opened.pending_id, choice)
    state = play._load(await play.store.read(cid))
    roll = snapshot(state).luck.rolls[-1]
    assert roll.modifier == modifier and roll.chosen_total == total
    assert roll.chosen_dice and sum(roll.chosen_dice) + roll.modifier == roll.chosen_total
    result = private_result(state)
    assert json.loads(result.reaction_json or "{}")["reaction"]["outcome"] == band


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "modifier,faces,total,band,learned,index",
    [
        (2, (5, 5, 5, 6, 6, 5, 4, 4, 4), 19, "excellent", True, 1),
        (0, (3, 3, 3, 2, 2, 2, 3, 3, 3), 9, "poor", False, 0),
    ],
)
async def test_modified_best_and_unhelpful_tie_spend_one_legal_use(
    tmp_path: Path,
    backend: str,
    modifier: int,
    faces: tuple[int, ...],
    total: int,
    band: str,
    learned: bool,
    index: int,
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid, authored=source(modifier=modifier))
    play.rng = RecordedDice(faces)
    await choose(play, cid, opened.pending_id)
    state = play._load(await play.store.read(cid))
    result = private_result(state)
    trace = json.loads(result.reaction_json or "{}")["reaction"]
    assert (trace["total"], trace["outcome"]) == (total, band)
    assert result.luck and result.luck.chosen_index == index
    assert (("a", "answer") in state.world.knowledge) is learned
    assert len(snapshot(state).luck.receipts) == len(real_play_clock(state).cooldowns) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("active,total,learned", [(True, 13, True), (False, 12, False)])
async def test_explicit_passive_reaction_retains_appearance_but_not_charisma(
    tmp_path: Path, backend: str, active: bool, total: int, learned: bool
) -> None:
    cid, play = await fixture(
        tmp_path,
        backend,
        social_traits=(
            Purchase(definition_id="trait:charisma", amount=1),
            Purchase(definition_id="trait:appearance-attractive", amount=1),
        ),
    )
    authored = source().model_copy(update={"active_interaction": active})
    _, opened = await prepare(play, cid, authored=authored)
    play.rng = RecordedDice((3, 4, 4))
    await choose(play, cid, opened.pending_id, "resolve")
    state = play._load(await play.store.read(cid))
    trace = json.loads(private_result(state).reaction_json or "{}")["reaction"]
    assert trace["total"] == total
    assert (("a", "answer") in state.world.knowledge) is learned
    assert (
        any(modifier["source_id"] == "trait:charisma" for modifier in trace["modifiers"]) is active
    )
    assert any(modifier["kind"] == "appearance" for modifier in trace["modifiers"])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("sapient,total,learned", [(True, 13, True), (False, 12, False)])
async def test_explicit_sapient_audience_controls_generic_charisma(
    tmp_path: Path, backend: str, sapient: bool, total: int, learned: bool
) -> None:
    cid, play = await fixture(
        tmp_path, backend, social_traits=(Purchase(definition_id="trait:charisma", amount=1),)
    )
    _, opened = await prepare(play, cid, authored=source().model_copy(update={"sapient": sapient}))
    play.rng = RecordedDice((4, 4, 4))
    await choose(play, cid, opened.pending_id, "resolve")
    state = play._load(await play.store.read(cid))
    assert json.loads(private_result(state).reaction_json or "{}")["reaction"]["total"] == total
    assert (("a", "answer") in state.world.knowledge) is learned
