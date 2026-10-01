"""B96 author provenance is distinct from current durable approval authority."""

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_play
from test_actions import campaign, world
from test_unusual_background_admission import configured, decision, proposal

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import ApproveCharacter, PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def prepare(path: Path, backend: str = "sqlite") -> tuple[str, PlayService]:
    package, reviewer = configured(decision(50))
    reviewer = PowerReviewer(reviewer.compiler, reviewer.policy, frozenset({"gm", "successor"}))
    compiler = reviewer.compiler
    engine = ActionEngine(
        reviewer,
        ResourceEngine(world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        ActionRules(id="background-authority", version=1),
    )
    play = build_play(path, engine, backend=backend)
    initial = campaign(engine)
    state = await seed_play(
        play,
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal(background=True)),),
    )
    assert state.actors[0].approval is None
    return initial["id"], play


def approval(*, revision: int = 0, principal: str = "gm") -> ApproveCharacter:
    return ApproveCharacter(
        id="approve-background",
        actor_id=principal,
        target_actor_id="a",
        expected_revision=revision,
        reason="Approve the pinned background benefit and its additional 50 points",
    )


async def revoke_author_seat(play: PlayService, cid: str) -> None:
    """Another durable membership command revokes the original author's seat."""
    revision = (await play.store.read(cid))["revision"]

    def resolve(current: Campaign) -> CommandReceipt:
        state = play._load(current)
        play.commit(
            current,
            state.model_copy(
                update={
                    "revision": revision + 1,
                    "resources": state.resources.model_copy(update={"revision": revision + 1}),
                    "members": tuple(
                        member.model_copy(update={"role": "spectator"})
                        if member.principal_id == "gm"
                        else member
                        for member in state.members
                    ),
                }
            ),
        )
        return CommandReceipt(action="v1-membership", outcome="Author's campaign seat revoked")

    await play.store.commit_turn(
        cid, "revoke-author-seat", revision, "revoke-author-seat", resolve, actor_id="successor"
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("retry", [False, True])
async def test_revoked_campaign_gm_cannot_approve_or_retry(
    tmp_path: Path, backend: str, retry: bool
) -> None:
    cid, original = await prepare(tmp_path, backend)
    command = approval()
    if retry:
        await original.approve(cid, command, principal_id="gm")
    await revoke_author_seat(original, cid)
    before = await original.store.read(cid)
    if not retry:
        command = approval(revision=before["revision"])
    restarted = build_play(tmp_path, original.engine, backend=backend)
    with pytest.raises(ValidationError, match="authority"):
        await restarted.approve(cid, command, principal_id="gm")
    assert await original.store.read(cid) == before
    saved = original._load(before)
    assert next(member.role for member in saved.members if member.principal_id == "gm") == (
        "spectator"
    )
    assert len(saved.approvals) == int(retry)
    assert len(await played(original.store, cid)) == 1 + int(retry)
    assert await original.store.replay(cid) == before


class RevokingStore(AsyncSQLiteStore):
    """A separate connection revokes after early authorization, before lookup."""

    revoke: Callable[[], Awaitable[None]] | None = None

    async def duplicate(self, cid: str, command_id: str, text: str) -> Campaign | None:
        if self.revoke is not None:
            callback, self.revoke = self.revoke, None
            await callback()
        return await super().duplicate(cid, command_id, text)


@pytest.mark.integration
@pytest.mark.parametrize("retry", [False, True])
async def test_approval_rechecks_seat_at_commit_and_early_retry(
    tmp_path: Path, retry: bool
) -> None:
    cid, original = await prepare(tmp_path)
    command = approval()
    if retry:
        await original.approve(cid, command, principal_id="gm")
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    outer = build_play(tmp_path, original.engine, store=store)
    store.revoke = lambda: revoke_author_seat(original, cid)
    # Matching the post-revocation revision proves authority blocks the write;
    # an ordinary stale-revision conflict must not make this test pass.
    if not retry:
        command = approval(revision=1)
    with pytest.raises(ValidationError, match="authority"):
        await outer.approve(cid, command, principal_id="gm")
    saved = original._load(await original.store.read(cid))
    assert saved.revision == 1 + int(retry)
    assert len(saved.approvals) == int(retry)
    assert len(await played(original.store, cid)) == 1 + int(retry)


class AfterMissStore(AsyncSQLiteStore):
    """Another connection approves, then revokes, after an early duplicate miss."""

    after_miss: Callable[[], Awaitable[None]] | None = None

    async def duplicate(self, cid: str, command_id: str, text: str) -> Campaign | None:
        result = await super().duplicate(cid, command_id, text)
        if result is None and self.after_miss is not None:
            callback, self.after_miss = self.after_miss, None
            await callback()
        return result


@pytest.mark.integration
async def test_transaction_duplicate_rechecks_current_approver_seat(tmp_path: Path) -> None:
    cid, original = await prepare(tmp_path)
    store = AfterMissStore(tmp_path / "runtime.sqlite", 10)
    outer = build_play(tmp_path, original.engine, store=store)
    command = approval()

    async def race() -> None:
        await original.approve(cid, command, principal_id="gm")
        await revoke_author_seat(original, cid)

    store.after_miss = race
    with pytest.raises(ValidationError, match="authority"):
        await outer.approve(cid, command, principal_id="gm")
    saved = original._load(await original.store.read(cid))
    assert saved.revision == 2 and len(saved.approvals) == 1
    assert len(await played(original.store, cid)) == 2


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seated_successor_approves_unchanged_historical_background(
    tmp_path: Path, backend: str
) -> None:
    cid, original = await prepare(tmp_path, backend)
    authored = original.engine.reviewer.compiler.unusual_background
    assert authored is not None and authored.gm_id == "gm"
    pinned = (await original.store.read(cid))["rules_ref"]
    await revoke_author_seat(original, cid)
    restarted = build_play(tmp_path, original.engine, backend=backend)
    command = approval(revision=1, principal="successor")
    approved = await restarted.approve(cid, command, principal_id="successor")
    assert approved.approver_id == "successor" and approved.decision == "gm"
    assert await restarted.approve(cid, command, principal_id="successor") == approved
    active = await restarted.execute(
        cid,
        Wait(id="active-background", actor_id="a", expected_revision=2, ticks=1),
        principal_id="a",
    )
    assert active.status == "committed"
    saved = await original.store.read(cid)
    assert saved["rules_ref"] == pinned
    assert restarted.engine.reviewer.compiler.unusual_background == authored
    assert original._load(saved).actors[0].approval == approved
    assert len(await played(original.store, cid)) == 3
    assert await original.store.replay(cid) == saved
