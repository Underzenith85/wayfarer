"""Selected secret campaign reactions commit their actual case/loyalty/knowledge."""

import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_orchestrator, build_play, build_runtime, seed_campaign
from test_campaign_reaction_continuations import (
    administration_case,
    initial_case,
    law_case,
    rescue_case,
)
from test_reaction_task_host import choose, opaque
from test_secret_task_host import private_result
from test_task_host import fixture as task_fixture
from test_wave9 import FakeProvider

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.campaign.reactions import (
    CampaignReactionSource,
    PreparedCampaignReaction,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import PrepareReaction, SecretReactionPending
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands

Role = Literal["initial-loyalty", "rescue-loyalty", "law", "administration"]
Choice = Literal["use-luck", "resolve", "cancel"]


async def fixture(
    path: Path, backend: str, role: Role, old: int = 14, *, social_traits: tuple[Purchase, ...] = ()
) -> tuple[str, PlayService, CampaignReactionSource]:
    engine, domain, source = (
        initial_case()
        if role == "initial-loyalty"
        else rescue_case(old)
        if role == "rescue-loyalty"
        else law_case()
        if role == "law"
        else administration_case()
    )
    cid, original = await task_fixture(path / "base", social_traits=social_traits)
    rules = original.engine.rules.model_copy(
        update={
            "administration": engine.rules.administration,
            "economics": engine.rules.economics,
            "law": engine.rules.law,
        }
    )
    bound = ActionEngine(original.engine.reviewer, original.engine.resources, rules)
    play = build_play(
        path, bound, backend=backend, rng=RecordedDice(()), instants=original.instants
    )
    saved = await original.store.read(cid)
    state = original._load(saved)
    state = state.model_copy(
        update={
            "configuration_digest": bound.digest,
            "members": tuple(
                member.model_copy(update={"role": "spectator", "actor_ids": ()})
                if member.principal_id == "b"
                else member
                for member in state.members
            ),
            "economics": domain.economics,
            "law": domain.law,
            "administration": domain.administration,
            "world": replace(state.world, knowledge=domain.world.knowledge),
        }
    )
    play.commit(saved, state)
    await seed_campaign(play.store, saved)
    source = source.model_copy(
        update={"command": source.command.model_copy(update={"expected_revision": state.revision})}
    )
    return cid, play, source


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("role", ["initial-loyalty", "rescue-loyalty", "law", "administration"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_campaign_actual_consequences_and_frozen_prerequisites(
    tmp_path: Path, backend: str, role: Role, choice: Choice
) -> None:
    cid, play, source = await fixture(tmp_path, backend, role)
    before = play._load(await play.store.read(cid))
    pre_faces = (3, 3, 3) if role == "initial-loyalty" else ()
    dice = RecordedDice(pre_faces)
    play.rng = dice
    command = PrepareReaction(
        id="prepare", actor_id="a", expected_revision=before.revision, source=source
    )
    opened = await TaskService(play).execute(cid, command, principal_id="gm")
    assert dice.exhausted()
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, SecretReactionPending)
    prepared = PreparedCampaignReaction.model_validate_json(pending.preparation_json)
    assert (
        state.economics == before.economics
        and state.law == before.law
        and state.world == before.world
    )
    assert (prepared.frozen_search is not None) is (role == "initial-loyalty")
    if prepared.frozen_search:
        assert prepared.frozen_search.dice == (3, 3, 3)
    target = (
        (3, 3, 3, 5, 5, 5, 4, 4, 4)
        if choice == "use-luck"
        else (1, 2, 2)
        if role == "law" and choice == "resolve"
        else (3, 3, 3)
        if choice == "resolve"
        else ()
    )
    dice = RecordedDice(target)
    play.rng = dice
    _, result = await choose(play, cid, opened.pending_id, choice)
    assert dice.exhausted()
    if choice == "use-luck":
        opaque(result)
    final = play._load(await play.store.read(cid))
    if role == "initial-loyalty":
        assert len(final.economics.hirelings) == (choice != "cancel")
        if choice != "cancel":
            assert final.economics.hirelings[0].loyalty == (15 if choice == "use-luck" else 9)
    elif role == "rescue-loyalty":
        assert final.economics.hirelings[0].loyalty == (18 if choice == "use-luck" else 14)
        assert len(final.economics.loyalty_checks) == (choice != "cancel")
    elif role == "law":
        assert final.law.cases[0].status == (
            "acquitted"
            if choice == "use-luck"
            else "convicted"
            if choice == "resolve"
            else "arrested"
        )
        assert final.resources.game_time == (20 if choice != "cancel" else 0)
    else:
        assert (("a", "clue") in final.world.knowledge) is (choice == "use-luck")
        assert ("b", "clue") in final.world.knowledge
    assert len(snapshot(final).luck.receipts) == (choice == "use-luck")
    assert snapshot(final).pending is None
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("role", ["initial-loyalty", "rescue-loyalty", "law", "administration"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve", "cancel"])
async def test_campaign_seed_only_reexecution_and_current_private_views(
    tmp_path: Path, backend: str, role: Role, choice: Choice
) -> None:
    cid, play, source = await fixture(tmp_path, backend, role)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    # The source-independent first search roll is 2+5+1=8, below the approved IQ10.
    play.seeds = lambda: "03" * 32
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare",
            actor_id="a",
            expected_revision=play._load(initial).revision,
            source=source,
        ),
        principal_id="gm",
    )
    assert opened.status == "pending"
    await choose(play, cid, opened.pending_id, choice)
    runtime = build_runtime(play)
    orchestrator = build_orchestrator(runtime, FakeProvider())
    context, _, _ = await orchestrator.context(cid, "a", "a")
    view = json.dumps(await runtime.read(cid, principal_id="a"))
    events = json.dumps(
        [event.model_dump(mode="json") for event in await runtime.events(cid, principal_id="a")]
    )
    for secret in ('"dice"', '"attempts"', "frozen_search", "private_motive", "reaction_json"):
        assert secret not in context and secret not in view and secret not in events
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_good_rescue_never_reduces_existing_loyalty(tmp_path: Path, backend: str) -> None:
    cid, play, source = await fixture(tmp_path, backend, "rescue-loyalty", 16)
    state = play._load(await play.store.read(cid))
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare", actor_id="a", expected_revision=state.revision, source=source
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice((3, 3, 4, 2, 2, 2, 1, 1, 1))
    await choose(play, cid, opened.pending_id)
    state = play._load(await play.store.read(cid))
    assert state.economics.hirelings[0].loyalty == 16
    result = private_result(state)
    assert json.loads(result.reaction_json or "{}")["reaction"]["total"] == 13


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("role", ["initial-loyalty", "rescue-loyalty", "law", "administration"])
async def test_campaign_cancel_after_source_loss_closes_ordinary_source_identity(
    tmp_path: Path,
    backend: str,
    role: Role,
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.errors import ConflictError

    cid, play, source = await fixture(tmp_path, backend, role)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3) if role == "initial-loyalty" else ())
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare", actor_id="a", expected_revision=state.revision, source=source
        ),
        principal_id="gm",
    )
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) if actor.actor_id == "a" else actor
                    for actor in state.actors
                )
            }
        ),
    )
    play.rng = RecordedDice(())
    await choose(play, cid, opened.pending_id, "cancel")
    state = play._load(await play.store.read(cid))
    # Existing direct consumers see the cancelled original and draw no new prerequisites.
    _, outcome = play.engine.campaign.apply(
        state, source.command, rng=RecordedDice(()), system=True
    )
    assert outcome.status == "cancelled"
    with pytest.raises(ConflictError):
        play.engine.campaign.apply(
            state,
            source.command.model_copy(update={"expected_revision": state.revision}),
            rng=RecordedDice(()),
            system=True,
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "choice,applies_on,expected",
    [("use-luck", "grateful", 20), ("resolve", "any-reaction", 16), ("cancel", "any-reaction", 14)],
)
async def test_rescue_explicit_permanent_bonus_is_a_separate_atomic_consequence(
    tmp_path: Path,
    backend: str,
    choice: Choice,
    applies_on: str,
    expected: int,
) -> None:
    from wayfarer.engine.simulation.campaign.reactions import RescuePermanentBonus

    cid, play, source = await fixture(tmp_path, backend, "rescue-loyalty")
    bonus = RescuePermanentBonus.model_validate(
        {
            "amount": 2,
            "rescuer_actor_id": "a",
            "loss": "serious-injury",
            "applies_on": applies_on,
            "reason": "The GM assigned a permanent bonus for the rescuer's serious injury",
        }
    )
    source = source.model_copy(update={"rescue_bonus": bonus})
    state = play._load(await play.store.read(cid))
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare", actor_id="a", expected_revision=state.revision, source=source
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice(
        (3, 3, 3, 5, 5, 5, 4, 4, 4)
        if choice == "use-luck"
        else (3, 3, 3)
        if choice == "resolve"
        else ()
    )
    command, result = await choose(play, cid, opened.pending_id, choice)
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.economics.hirelings[0].loyalty == expected
    play.rng = RecordedDice(())
    assert (
        await TaskService(play).execute(
            cid, command, principal_id="a" if choice == "use-luck" else "gm"
        )
        == result
    )
    assert await play.store.read(cid) == saved == await play.store.replay(cid)
