"""Unrolled secret choices require present authority/context and atomic spending."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay, RevokingStore
from test_secret_task_host import Choice, choose_secret, prepare, private_result
from test_task_host import fixture

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.symptoms import SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import (
    ChooseSecretTaskCheck,
    PrepareSecretTaskCheck,
    SecretTaskPending,
    SetRealPlayClock,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.postgres import AsyncPostgresStore


def revoke_owner(state: PlayState) -> PlayState:
    return state.model_copy(
        update={
            "members": tuple(
                member.model_copy(update={"actor_ids": ()})
                if member.principal_id == "a"
                else member
                for member in state.members
            )
        }
    )


def revoke_seat(state: PlayState) -> PlayState:
    return state.model_copy(
        update={
            "members": tuple(
                member.model_copy(update={"role": "spectator"})
                if member.principal_id == "gm"
                else member
                for member in state.members
            )
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,seconds", [(15, 3600), (30, 1800), (60, 600)])
async def test_prepared_before_deadline_can_declare_at_exact_microsecond_after_pause(
    tmp_path: Path, backend: str, points: int, seconds: int
) -> None:
    cid, play = await fixture(tmp_path, backend, points=points)
    origin = real_play_clock(play._load(await play.store.read(cid))).observed_at_us
    assert origin is not None
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    _, first = await prepare(play, cid)
    play.rng = RecordedDice((6, 6, 6, 5, 5, 5, 2, 2, 3))
    await choose_secret(play, cid, first.pending_id)
    clock = real_play_clock(play._load(await play.store.read(cid)))
    deadline = seconds * 1_000_000 + 900001
    assert clock.cooldowns[0].available_at_microseconds == deadline
    now[0] += 1
    play.rng = RecordedDice(())
    _, second = await prepare(play, cid, "second")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert (
        isinstance(pending, SecretTaskPending) and pending.prepared_elapsed_microseconds == 900002
    )
    for identifier, running, at in (("pause", False, 1_000_000), ("resume-again", True, 5_000_000)):
        now[0] = origin + at
        revision = play._load(await play.store.read(cid)).revision
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id=identifier, actor_id="gm", expected_revision=revision, running=running
            ),
            principal_id="gm",
        )
        assert snapshot(play._load(await play.store.read(cid))).pending == pending
    now[0] = origin + deadline + 4_000_000 - 1
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="cooling down"):
        await choose_secret(play, cid, second.pending_id, identifier="second-use")
    assert await play.store.read(cid) == before
    now[0] += 1
    play.rng = RecordedDice((6, 6, 6, 5, 5, 5, 2, 2, 3))
    await choose_secret(play, cid, second.pending_id, identifier="second-use")
    state = play._load(await play.store.read(cid))
    assert (
        real_play_clock(state).cooldowns[0].available_at_microseconds
        == deadline + seconds * 1_000_000
    )
    assert state.resources.game_time == 2


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice,count", [("use-luck", 9), ("resolve", 3), ("cancel", 0)])
async def test_exact_target_draw_counts(
    tmp_path: Path, backend: str, choice: Choice, count: int
) -> None:
    cid, play = await fixture(tmp_path, backend)
    dice = RecordedDice(())
    play.rng = dice
    _, begun = await prepare(play, cid)
    assert dice.exhausted()
    dice = RecordedDice((3,) * count)
    play.rng = dice
    await choose_secret(play, cid, begun.pending_id, choice)
    assert dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve"])
async def test_failure_after_candidate_commit_rolls_back_every_effect(
    tmp_path: Path, backend: str, choice: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend, fatigue_cost=2)
    _, begun = await prepare(play, cid)
    before = await play.store.read(cid)
    events, history = await play.store.stream(cid), await play.store.history(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((2, 2, 3) * 3), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose_secret(failing, cid, begun.pending_id, choice)
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.stream(cid) == events and await play.store.history(cid) == history
    play.rng = RecordedDice((2, 2, 3) * 3)
    await choose_secret(play, cid, begun.pending_id, choice)
    state = play._load(await play.store.read(cid))
    assert state.world.knowledge.count(("a", "clue")) == 1
    assert len(snapshot(state).luck.receipts) == (choice == "use-luck")
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 8


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_only_owner_can_spend_and_only_current_gm_can_prepare_resolve_cancel(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    command = PrepareSecretTaskCheck(
        id="secret", actor_id="a", expected_revision=state.revision, check_id="carpentry-inspect"
    )
    with pytest.raises(ValidationError, match="director"):
        await TaskService(play).execute(cid, command, principal_id="a")
    _, begun = await prepare(play, cid)
    before = await play.store.read(cid)
    for principal in ("gm", "b"):
        with pytest.raises(AuthorizationError):
            await choose_secret(play, cid, begun.pending_id, principal=principal)
    for choice in ("resolve", "cancel"):
        with pytest.raises(ValidationError, match="director"):
            await choose_secret(play, cid, begun.pending_id, choice, principal="a")
    assert await play.store.read(cid) == before
    await change(play, cid, revoke_seat)
    with pytest.raises(ValidationError, match="director"):
        await TaskService(play).execute(cid, command, principal_id="gm")
    with pytest.raises(ValidationError, match="director"):
        await choose_secret(play, cid, begun.pending_id, "cancel")
    play.rng = RecordedDice((2, 2, 3) * 3)
    owner_command, _ = await choose_secret(play, cid, begun.pending_id)
    await change(play, cid, revoke_owner)
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, owner_command, principal_id="a")


class RevokingPostgresStore(AsyncPostgresStore):
    revoke: Callable[[], Awaitable[None]] | None = None

    async def duplicate(self, cid: str, command_id: str, text: str) -> Campaign | None:
        if self.revoke is not None:
            revoke, self.revoke = self.revoke, None
            await revoke()
        return await super().duplicate(cid, command_id, text)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("retry", [False, True])
async def test_owner_control_is_rechecked_under_transaction_and_retry(
    tmp_path: Path, backend: str, retry: bool
) -> None:
    cid, original = await fixture(tmp_path, backend)
    _, begun = await prepare(original, cid)
    assert begun.pending_id is not None
    state = original._load(await original.store.read(cid))
    command = ChooseSecretTaskCheck(
        id="declare",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
    )
    if retry:
        original.rng = RecordedDice((2, 2, 3) * 3)
        await TaskService(original).execute(cid, command, principal_id="a")
    store = (
        RevokingPostgresStore(os.environ["WAYFARER_TEST_DATABASE_URL"], 10)
        if backend == "postgres"
        else RevokingStore(tmp_path / "runtime.sqlite", 10)
    )
    play = build_play(
        tmp_path, original.engine, store=store, rng=RecordedDice(()), instants=original.instants
    )
    store.revoke = lambda: change(original, cid, revoke_owner)
    if not retry:
        command = command.model_copy(update={"expected_revision": state.revision + 1})
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="a")
    state = original._load(await original.store.read(cid))
    assert len(snapshot(state).luck.receipts) == int(retry)
    if not retry:
        await choose_secret(original, cid, begun.pending_id, "cancel")


async def reapprove(play: PlayService, cid: str, *, luck: bool, skill_points: int = 8) -> None:
    def update(state: PlayState) -> PlayState:
        actor = next(actor for actor in state.actors if actor.actor_id == "a")
        purchases = tuple(
            purchase.model_copy(update={"amount": skill_points})
            if purchase.definition_id == "skill:carpentry"
            else purchase
            for purchase in actor.proposal.draft.purchases
            if luck or purchase.definition_id != "trait:advantage:luck"
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="a",
            revision=state.revision,
            approver_id="gm",
            reason="Updated approved task source",
        )
        build = play.engine.reviewer.compiler.compile(proposal.draft).build
        assert build is not None
        return state.model_copy(
            update={
                "approvals": state.approvals + (approval,),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            owner.model_copy(
                                update={
                                    "definitions": tuple(p.definition_id for p in build.purchases)
                                }
                            )
                            if owner.actor_id == "a"
                            else owner
                            for owner in state.resources.owners
                        )
                    }
                ),
                "actors": tuple(
                    current.model_copy(update={"proposal": proposal, "approval": approval})
                    if current.actor_id == "a"
                    else current
                    for current in state.actors
                ),
            }
        )

    await change(play, cid, update)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change_kind", ["approval", "luck", "target", "symptom"])
async def test_current_sources_reject_before_dice_but_remain_cancellable(
    tmp_path: Path, backend: str, change_kind: str
) -> None:
    cid, play = await fixture(tmp_path, backend, fatigue_cost=2)
    _, begun = await prepare(play, cid)
    if change_kind == "approval":
        await change(
            play,
            cid,
            lambda state: state.model_copy(
                update={
                    "actors": tuple(
                        actor.model_copy(update={"approval": None})
                        if actor.actor_id == "a"
                        else actor
                        for actor in state.actors
                    )
                }
            ),
        )
    elif change_kind == "symptom":
        await change(
            play,
            cid,
            lambda state: state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "symptom_effects": (
                                SymptomEffect(
                                    id="iq-loss",
                                    pool_id="hp:a",
                                    actor_id="a",
                                    source_id="illness",
                                    spec=SymptomSpec(
                                        kind="attribute-penalty", attribute="iq", level=2
                                    ),
                                    active=True,
                                ),
                            )
                        }
                    )
                }
            ),
        )
    else:
        await reapprove(
            play, cid, luck=change_kind != "luck", skill_points=12 if change_kind == "target" else 8
        )
    before = await play.store.read(cid)
    with pytest.raises((ValidationError, ConflictError)):
        await choose_secret(play, cid, begun.pending_id)
    assert await play.store.read(cid) == before
    await choose_secret(play, cid, begun.pending_id, "cancel")
    state = play._load(await play.store.read(cid))
    assert not snapshot(state).luck.receipts and snapshot(state).pending is None
    assert not state.world.knowledge and state.resources.game_time == 1
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 8


@pytest.mark.parametrize(
    "modifiers,action,allowed",
    [
        (("active",), "inspect", True),
        (("aspected-social",), "social", True),
        (("aspected-social",), "inspect", False),
        (("defensive",), "inspect", False),
    ],
)
async def test_source_limitations_use_secret_task_class_before_dice(
    tmp_path: Path, modifiers: tuple[str, ...], action: str, allowed: bool
) -> None:
    cid, play = await fixture(
        tmp_path, modifiers=modifiers, action="social" if action == "social" else "inspect"
    )
    _, begun = await prepare(play, cid)
    if allowed:
        play.rng = RecordedDice((2, 2, 3) * 3)
        await choose_secret(play, cid, begun.pending_id)
        assert private_result(play._load(await play.store.read(cid))).luck
    else:
        before = await play.store.read(cid)
        with pytest.raises(ValidationError, match="aspect|Defensive"):
            await choose_secret(play, cid, begun.pending_id)
        assert await play.store.read(cid) == before
        play.rng = RecordedDice((6, 6, 6))
        await choose_secret(play, cid, begun.pending_id, "resolve")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_exact_retry_uses_current_player_privacy_after_gm_demotion(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.engine.reviewer.gm_ids = frozenset({"gm", "a"})

    def role(state: PlayState, value: Literal["gm", "player"]) -> PlayState:
        return state.model_copy(
            update={
                "members": tuple(
                    member.model_copy(
                        update={"role": value, "actor_ids": ("a",) if value == "player" else ()}
                    )
                    if member.principal_id == "a"
                    else member
                    for member in state.members
                )
            }
        )

    await change(play, cid, lambda state: role(state, "gm"))
    _, begun = await prepare(play, cid)
    play.rng = RecordedDice((6, 6, 6, 5, 5, 5, 2, 2, 3))
    command, result = await choose_secret(play, cid, begun.pending_id)
    assert result.check and result.luck
    await change(play, cid, lambda state: role(state, "player"))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    retried = await TaskService(play).execute(cid, command, principal_id="a")
    assert retried.check is None and retried.luck is None
    assert retried.action is None and retried.activity is None
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_exertion_precedes_secret_opportunity(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            pool.model_copy(update={"current": 0}) if pool.id == "fp:a" else pool
                            for pool in state.resources.pools
                        )
                    }
                )
            }
        ),
    )
    play.rng = RecordedDice((6, 5, 5))
    _, result = await prepare(play, cid)
    state = play._load(await play.store.read(cid))
    assert result.status == "interrupted" and result.check is None and result.pending_id is None
    assert state.resources.game_time == 0 and snapshot(state).pending is None
    assert not snapshot(state).luck.rolls
    fp = next(pool for pool in state.resources.pools if pool.id == "fp:a")
    assert fp.fatigue and fp.fatigue.collapsed


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_gm_trust_loss_blocks_new_commands_reads_and_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    command, begun = await prepare(play, cid)
    before = await play.store.read(cid)
    play.engine.reviewer.gm_ids = frozenset()
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).execute(cid, command, principal_id="gm")
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).pending(cid, principal_id="gm")
    for choice in ("resolve", "cancel"):
        with pytest.raises(ValidationError, match="GM authority"):
            await choose_secret(play, cid, begun.pending_id, choice)
    assert await play.store.read(cid) == before
    # The owner's own authorization does not depend on a GM spending for them.
    play.rng = RecordedDice((2, 2, 3) * 3)
    _, result = await choose_secret(play, cid, begun.pending_id)
    assert result.status == "completed" and result.check is None and result.luck is None
