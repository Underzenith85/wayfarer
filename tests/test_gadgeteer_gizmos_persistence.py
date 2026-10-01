"""B57-58 canonical inventory/secret receipts through both durable command adapters."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign, world
from test_gadgeteer_gizmos import context, gizmo_compiler, gizmo_package
from test_statistics import gurps_draft

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import HumanBody
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState, UseItem, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.events import ResourceChanged, visible
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.traits.gadgeteer_gizmos import (
    GadgeteerGizmoApproval,
    gm_gizmo_rolls,
)
from wayfarer.engine.simulation.traits.gizmos import BeginGizmoSession, RevealGizmo, history
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.gadgeteer_gizmos import GadgeteerGizmoService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def prepare(path: Path, backend: str = "sqlite") -> tuple[str, PlayService]:
    compiler = gizmo_compiler()
    equipment, resources, _ = context()
    resource_engine = ResourceEngine(
        world(),
        RulesCatalog((gizmo_package(),)),
        compiler.rules,
        compiler.policy,
        tuple(spec for key, spec in equipment.specs.items() if key != "sword"),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="gizmo-test", version=1), frozenset({"gm"})),
        resource_engine,
        ActionRules(id="gizmo-test", version=1, consumables=("bag",)),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    await seed_play(
        play,
        initial,
        world(),
        resources.model_copy(update={"revision": 0, "events": (), "receipts": ()}),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(
                    draft=gurps_draft(
                        Purchase(definition_id="trait:advantage:gizmos"),
                        Purchase(definition_id="trait:advantage:gadgeteer"),
                        Purchase(definition_id="skill:carpentry", amount=1),
                    )
                ),
                body=HumanBody(anatomy="human"),
            ),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    await GadgeteerGizmoService(play, approved).execute(
        initial["id"],
        BeginGizmoSession(id="open", actor_id="gm", expected_revision=0, session_id="one"),
        principal_id="gm",
    )
    return initial["id"], play


def approved(play: PlayService, state: PlayState, command: RevealGizmo) -> GadgeteerGizmoApproval:
    return context()[2]


def forbidden(play: PlayService, state: PlayState, command: RevealGizmo) -> GadgeteerGizmoApproval:
    raise AssertionError("A committed retry cannot resolve approval or roll again")


def craft() -> RevealGizmo:
    return RevealGizmo(
        id="make", actor_id="a", expected_revision=1, session_id="one", eligibility_id="craft"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dice,disabled,hp", [((1, 2, 3), False, 10), ((4, 4, 4), True, 10), ((6, 6, 6), True, 8)]
)
async def test_atomic_roll_materials_condition_and_backfire_survive_restart(
    tmp_path: Path, backend: str, dice: tuple[int, ...], disabled: bool, hp: int
) -> None:
    cid, play = await prepare(tmp_path, backend)
    play.rng = RecordedDice(dice)
    service = GadgeteerGizmoService(play, approved)
    outcomes = await asyncio.gather(
        *(service.execute(cid, craft(), principal_id="gm") for _ in range(3))
    )
    assert all(outcome == outcomes[0] for outcome in outcomes)
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.revision == state.resources.revision == 2
    assert [(item.id, item.quantity) for item in state.resources.items] == [
        ("raw", 1),
        ("device", 1),
    ]
    device = state.resources.items[1]
    assert device.owner_id == "a" and device.condition is not None
    assert device.condition.disabled is disabled
    assert next(pool.current for pool in state.resources.pools if pool.id == "hp:a") == hp
    assert history(state.resources)[-1].uses_remaining == 0
    roll = gm_gizmo_rolls(state.resources, gm_authorized=True)[0]
    assert roll.check is not None and roll.check.dice == dice and roll.check.effective_target == 8
    assert roll.hp_lost == 10 - hp
    with pytest.raises(ValidationError, match="GM visibility"):
        gm_gizmo_rolls(state.resources)
    records = await played(play.store, cid)
    assert len(records) == 2 and records[-1].resulting_revision == 2
    assert records[-1].actor_id == "gm" and records[-1].entropy_seed is not None
    events = await play.store.stream(cid)
    secret = [
        event.event
        for event in events
        if isinstance(event.event, ResourceChanged)
        and event.event.fact.id.startswith("gizmo-roll:")
    ]
    assert len(secret) == 1
    assert visible(secret[0], CampaignMember(principal_id="gm", role="gm"))
    assert not visible(
        secret[0], CampaignMember(principal_id="alice", role="player", actor_ids=("a",))
    )
    runtime = build_runtime(play)
    view = await runtime.read(cid, principal_id="alice")
    stream = await runtime.events(cid, principal_id="alice")
    for private_name in ("gizmo-roll:", "effective_target", "hp_lost", "dice"):
        assert private_name not in json.dumps(view)
        assert all(private_name not in event.model_dump_json() for event in stream)
    # New connection/session handles prove this is a durable checkpoint, not a helper cache.
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restarted.store.replay(cid) == saved == await restarted.store.read(cid)
    assert (
        await GadgeteerGizmoService(restarted, forbidden).execute(cid, craft(), principal_id="gm")
        == outcomes[0]
    )
    assert await restarted.store.read(cid) == saved
    for principal in ("alice", "outsider"):
        with pytest.raises((ValidationError, NotFoundError)):
            await service.execute(cid, craft(), principal_id=principal)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, craft().model_copy(update={"eligibility_id": "changed"}), principal_id="gm"
        )
    with pytest.raises(ConflictError):
        await service.execute(cid, craft().model_copy(update={"id": "stale"}), principal_id="gm")
    if disabled:
        rejected = await restarted.execute(
            cid,
            UseItem(id="use-failed", actor_id="a", expected_revision=2, item_id="device"),
            principal_id="a",
        )
        assert rejected.status == "rejected" and rejected.code == "item.unavailable"
        assert await restarted.store.read(cid) == saved
    # A later action cannot refresh uses; exact retries still read the original result.
    await restarted.execute(
        cid, Wait(id="later", actor_id="a", expected_revision=2, ticks=1), principal_id="a"
    )
    later = await restarted.store.read(cid)
    assert (
        await GadgeteerGizmoService(restarted, forbidden).execute(cid, craft(), principal_id="gm")
        == outcomes[0]
    )
    assert await restarted.store.read(cid) == later
    with pytest.raises(ValidationError, match="available use"):
        await GadgeteerGizmoService(restarted, approved).execute(
            cid,
            craft().model_copy(update={"id": "again", "expected_revision": 3}),
            principal_id="gm",
        )


class FailingCommitPlay(PlayService):
    def commit(self, campaign: Campaign, state: PlayState) -> None:
        super().commit(campaign, state)
        raise RuntimeError("crash after candidate checkpoint")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_and_public_changes_rollback_together(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    before = await play.store.read(cid)
    records = await played(play.store, cid)
    events = await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((6, 6, 6)))
    service = GadgeteerGizmoService(failing, approved)
    # Build the plan directly to inject the failure at the actual commit boundary.
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failing,
            cid,
            service.plan(failing, failing._load(before), craft(), principal_id="gm"),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await played(play.store, cid) == records
    assert await play.store.stream(cid) == events
    play.rng = RecordedDice((1, 2, 3))
    await GadgeteerGizmoService(play, approved).execute(cid, craft(), principal_id="gm")
    assert (
        len(gm_gizmo_rolls(play._load(await play.store.read(cid)).resources, gm_authorized=True))
        == 1
    )


async def revoke_gm(play: PlayService, cid: str, revision: int) -> None:
    def resolve(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        play.commit(
            campaign,
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
        return CommandReceipt(action="v1-membership", outcome="revoked")

    await play.store.commit_turn(cid, "revoke", revision, "revoke", resolve, actor_id="gm")


class RevokingStore(AsyncSQLiteStore):
    """Revoke the director after authorization, before duplicate/transaction lookup."""

    revoke: Callable[[], Awaitable[None]] | None = None

    async def duplicate(self, cid: str, command_id: str, text: str) -> Campaign | None:
        if self.revoke is not None:
            revoke, self.revoke = self.revoke, None
            await revoke()
        return await super().duplicate(cid, command_id, text)


@pytest.mark.parametrize("retry", [False, True])
async def test_direct_execute_rechecks_revoked_gm_on_commit_and_retry(
    tmp_path: Path, retry: bool
) -> None:
    cid, original = await prepare(tmp_path)
    if retry:
        original.rng = RecordedDice((1, 2, 3))
        await GadgeteerGizmoService(original, approved).execute(cid, craft(), principal_id="gm")
    revision = 2 if retry else 1
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    command = craft() if retry else craft().model_copy(update={"expected_revision": revision + 1})
    with pytest.raises(ValidationError, match="director authority"):
        await GadgeteerGizmoService(play, forbidden).execute(cid, command, principal_id="gm")
    saved = await original.store.read(cid)
    assert saved["revision"] == revision + 1
    rolls = gm_gizmo_rolls(original._load(saved).resources, gm_authorized=True)
    assert len(rolls) == int(retry)
    assert len(await played(original.store, cid)) == revision + 1


class AfterMissStore(AsyncSQLiteStore):
    """Another connection wins and revokes the GM after the early lookup misses."""

    after_miss: Callable[[], Awaitable[None]] | None = None

    async def duplicate(self, cid: str, command_id: str, text: str) -> Campaign | None:
        result = await super().duplicate(cid, command_id, text)
        if result is None and self.after_miss is not None:
            callback, self.after_miss = self.after_miss, None
            await callback()
        return result


@pytest.mark.parametrize("dedicated_replay", [False, True])
async def test_transaction_duplicate_rechecks_gm_before_either_result_callback(
    tmp_path: Path, dedicated_replay: bool
) -> None:
    cid, original = await prepare(tmp_path)
    original.rng = RecordedDice((1, 2, 3))
    store = AfterMissStore(tmp_path / "runtime.sqlite", 10)
    outer = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))

    async def race() -> None:
        await GadgeteerGizmoService(original, approved).execute(cid, craft(), principal_id="gm")
        await revoke_gm(original, cid, 2)

    store.after_miss = race
    service = GadgeteerGizmoService(outer, forbidden)
    with pytest.raises(ValidationError, match="director authority"):
        if dedicated_replay:
            state = outer._load(await store.read(cid))
            plan = service.plan(outer, state, craft(), principal_id="gm")
            await submit(outer, cid, replace(plan, replayed=plan.outcome), principal_id="gm")
        else:
            await service.execute(cid, craft(), principal_id="gm")
    saved = original._load(await original.store.read(cid))
    assert saved.revision == 3
    assert (
        next(member.role for member in saved.members if member.principal_id == "gm") == "spectator"
    )
    assert len(gm_gizmo_rolls(saved.resources, gm_authorized=True)) == 1
    assert len(await played(original.store, cid)) == 3
