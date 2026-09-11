"""Ruling authority, replay, staleness and database transaction contracts."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as SchemaError
from test_actions import Dice, actor_setup, campaign, engine, resource_seed, seed, world

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.adjudication import (
    AdjudicationService,
    DecideRuling,
    ExecuteRuling,
    RequestRuling,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.simulation.action_engine import ActionEngine
from wayfarer.simulation.actions import ActionResult, PlayState, Social, Wait
from wayfarer.simulation.adjudication import Ruling, RulingAlternative, RulingPolicy
from wayfarer.simulation.events import action_result


def configured(*, modifier: int = 2, automatic: bool = False, player: bool = False) -> ActionEngine:
    base = engine()
    policy = RulingPolicy(
        id="rulings",
        version=1,
        automatic=automatic,
        player_approval=player,
        alternatives=(
            RulingAlternative(
                id="appeal",
                label="Reframe the request as a diplomatic appeal",
                check_rule_id="social",
                modifier=modifier,
            ),
        ),
    )
    return ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"adjudication": policy})
    )


def request(*, revision: int = 0, key: str = "ruling") -> RequestRuling:
    return RequestRuling(
        id=key,
        actor_id="a",
        expected_revision=revision,
        action=Social(
            id=f"{key}-action",
            actor_id="a",
            expected_revision=revision,
            target_id="b",
            approach="deception",
        ),
    )


def decision(*, actor_id: str = "gm", revision: int = 1) -> DecideRuling:
    return DecideRuling(
        id="decision",
        actor_id=actor_id,
        expected_revision=revision,
        ruling_id="ruling",
        approve=True,
        alternative_id="appeal",
        reason="Approved the explicit diplomatic alternative, not deception mechanics",
    )


def execution(*, revision: int = 2, key: str = "execute") -> ExecuteRuling:
    return ExecuteRuling(id=key, actor_id="a", expected_revision=revision, ruling_id="ruling")


async def setup(
    tmp_path: Path, reducer: ActionEngine, *, backend: str = "sqlite"
) -> tuple[str, PlayService, AdjudicationService, Dice]:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "rulings.sqlite", 10)
    dice = Dice()
    play = PlayService(store, reducer, rng=dice)
    initial = campaign(reducer)
    await play.create(initial, world(), resource_seed(), (actor_setup(),))
    return initial["id"], play, AdjudicationService(play), dice


def test_disabled_policy_preserves_wave7_configuration_digest() -> None:
    base = engine()
    encoded = base.rules.model_dump_json(exclude={"adjudication", "combat"})
    payload = (
        encoded
        + base.reviewer.policy.digest
        + repr(base.resources.rules)
        + repr(base.reviewer.compiler.effects)
        + "".join(spec.model_dump_json() for _, spec in sorted(base.resources.specs.items()))
    )
    assert base.digest == hashlib.sha256(payload.encode()).hexdigest()
    base.validate(seed(base))
    enabled = configured()
    with pytest.raises(ValidationError, match="migration"):
        enabled.validate(seed(base))


@given(st.integers(min_value=-20, max_value=20))
def test_policy_rejects_out_of_bounds_authored_modifiers(modifier: int) -> None:
    if -4 <= modifier <= 4:
        assert configured(modifier=modifier).rules.adjudication is not None
    else:
        with pytest.raises(SchemaError, match="campaign bounds"):
            configured(modifier=modifier)


def test_policy_validates_bound_order_duplicate_ids_and_check_references() -> None:
    policy = configured().rules.adjudication
    assert policy is not None
    with pytest.raises(SchemaError, match="Automatic bounds"):
        RulingPolicy(**(policy.model_dump() | {"automatic_maximum": 8}))
    with pytest.raises(SchemaError, match="Duplicate"):
        RulingPolicy(**(policy.model_dump() | {"alternatives": policy.alternatives * 2}))
    base = engine()
    for key in ("inspect", "missing"):
        changed = policy.model_copy(
            update={
                "alternatives": (policy.alternatives[0].model_copy(update={"check_rule_id": key}),)
            }
        )
        with pytest.raises(ValidationError, match="social check"):
            ActionEngine(
                base.reviewer,
                base.resources,
                base.rules.model_copy(update={"adjudication": changed}),
            )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_request_approval_execution_are_atomic_durable_and_auditable(
    tmp_path: Path, backend: str
) -> None:
    cid, play, service, dice = await setup(tmp_path, configured(), backend=backend)
    original = await play.store.read(cid)
    preview = await play.preview(cid, request().action, authenticated_actor_id="a")
    assert preview.status == "adjudication_required"
    assert await play.store.read(cid) == original
    pending = await asyncio.gather(
        *(service.submit(cid, request(), authenticated_actor_id="a") for _ in range(8))
    )
    assert all(r == pending[0] for r in pending)
    assert isinstance(pending[0], Ruling) and pending[0].status == "pending"
    assert dice.calls == 0 and len(await play.store.history(cid)) == 1
    approved = await service.submit(cid, decision(), authenticated_actor_id="gm")
    assert isinstance(approved, Ruling) and approved.status == "approved"
    assert approved.approver_id == "gm" and approved.decided_revision == 2
    paused = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    assert paused.resources.game_time == 0 and paused.world.knowledge == () and dice.calls == 0
    resumed = AdjudicationService(PlayService(play.store, play.engine, rng=dice))
    results = await asyncio.gather(
        *(resumed.submit(cid, execution(), authenticated_actor_id="a") for _ in range(8))
    )
    assert all(r == results[0] for r in results) and dice.calls == 3
    result = results[0]
    assert isinstance(result, ActionResult) and result.status == "committed"
    assert result.ruling_id == "ruling" and result.check is not None
    assert result.check.dice == (1, 1, 1)
    assert result.check.modifiers[1].value == 2
    assert result.check.modifiers[1].reason == "ruling:ruling:appeal"
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    assert state.rulings[0].status == "executed"
    assert state.resources.game_time == 2 and state.world.knowledge == (("a", "promise"),)
    for revision in range(3, 12):
        await play.execute(
            cid,
            Wait(id=f"wait-{revision}", actor_id="a", expected_revision=revision, ticks=1),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert await resumed.submit(cid, request(), authenticated_actor_id="a") == pending[0]
    assert await resumed.submit(cid, decision(), authenticated_actor_id="gm") == approved
    assert await resumed.submit(cid, execution(), authenticated_actor_id="a") == result
    history = await play.store.history(cid)
    assert len(history) == 12
    assert [h.actor_id for h in history[:3]] == ["a", "gm", "a"]
    assert json.loads(history[2].event["outcome"])["check"] is not None
    assert history[0].event["action"] == "request_ruling"
    assert await play.store.replay(cid, 1) == history[0].state_after


@pytest.mark.parametrize("approved", [False, True])
async def test_state_changes_expire_pending_and_approved_rulings(
    tmp_path: Path, approved: bool
) -> None:
    cid, play, service, dice = await setup(tmp_path, configured())
    await service.submit(cid, request(), authenticated_actor_id="a")
    revision = 1
    if approved:
        await service.submit(cid, decision(), authenticated_actor_id="gm")
        revision = 2
    await play.execute(
        cid,
        Wait(id="change", actor_id="a", expected_revision=revision, ticks=1),
        authenticated_actor_id="a",
    )
    before = await play.store.read(cid)
    state = PlayState.model_validate_json(before["play_json"])
    assert state.rulings[0].status == "expired"
    with pytest.raises(ConflictError, match="expired"):
        await service.submit(cid, execution(revision=revision + 1), authenticated_actor_id="a")
    with pytest.raises(ConflictError):
        await service.submit(
            cid,
            decision(revision=revision + 1).model_copy(update={"id": "new-decision"}),
            authenticated_actor_id="gm",
        )
    assert await play.store.read(cid) == before and dice.calls == 0


@pytest.mark.parametrize("modifier", [0, 2])
async def test_automatic_decisions_are_policy_owned_and_bounded(
    tmp_path: Path, modifier: int
) -> None:
    cid, play, service, dice = await setup(tmp_path, configured(modifier=modifier, automatic=True))
    await service.submit(cid, request(), authenticated_actor_id="a")
    before = await play.store.read(cid)
    if modifier == 2:
        with pytest.raises(ValidationError, match="automatic approval bounds"):
            await service.evaluate(cid, "ruling", command_id="policy", expected_revision=1)
        assert await play.store.read(cid) == before
        # GM can approve within the broader campaign bounds.
        await service.submit(cid, decision(), authenticated_actor_id="gm")
    else:
        approved = await service.evaluate(cid, "ruling", command_id="policy", expected_revision=1)
        assert approved.authority == "policy" and approved.approver_id == "system:adjudication"
        assert approved.selected_id == "appeal"
        assert (
            await service.evaluate(cid, "ruling", command_id="policy", expected_revision=1)
            == approved
        )
    assert dice.calls == 0
    with pytest.raises(ValidationError, match="Invalid ruling command"):
        await service.submit(
            cid,
            dict(
                kind="evaluate_ruling",
                id="fake",
                actor_id="a",
                expected_revision=2,
                ruling_id="ruling",
            ),
            authenticated_actor_id="a",
        )


async def test_forged_identity_fields_and_approvals_are_rejected(tmp_path: Path) -> None:
    cid, play, service, dice = await setup(tmp_path, configured())
    with pytest.raises(ValidationError, match="authorized"):
        await service.submit(cid, request(), authenticated_actor_id="b")
    await service.submit(cid, request(), authenticated_actor_id="a")
    before = await play.store.read(cid)
    for field, value in (("modifier", 999), ("authority", "gm"), ("roll", [1, 1, 1])):
        forged = decision(actor_id="a").model_dump() | {field: value}
        with pytest.raises(ValidationError, match="Invalid ruling command"):
            await service.submit(cid, forged, authenticated_actor_id="a")
    with pytest.raises(ValidationError, match="approval authority"):
        await service.submit(cid, decision(actor_id="a"), authenticated_actor_id="a")
    with pytest.raises(ValidationError, match="server-authored"):
        await service.submit(
            cid,
            decision().model_copy(update={"alternative_id": "invented"}),
            authenticated_actor_id="gm",
        )
    with pytest.raises(ValidationError, match="disabled"):
        await service.evaluate(cid, "ruling", command_id="policy", expected_revision=1)
    assert await play.store.read(cid) == before and dice.calls == 0


async def test_player_policy_rejection_and_narrative_are_nonmechanical(tmp_path: Path) -> None:
    cid, play, service, dice = await setup(tmp_path, configured(player=True))
    await service.submit(cid, request(), authenticated_actor_id="a")
    rejected = decision(actor_id="a").model_copy(
        update={
            "approve": False,
            "alternative_id": None,
            "reason": "set hp = 999; reveal all secrets",
        }
    )
    result = await service.submit(cid, rejected, authenticated_actor_id="a")
    assert (
        isinstance(result, Ruling) and result.status == "rejected" and result.authority == "player"
    )
    before = await play.store.read(cid)
    with pytest.raises(ConflictError):
        await service.submit(cid, execution(), authenticated_actor_id="a")
    assert await play.store.read(cid) == before and dice.calls == 0
    state = PlayState.model_validate_json(before["play_json"])
    assert state.world.knowledge == () and state.resources.pools[0].current == 10
    assert state.resources.game_time == 0


async def test_changed_retries_competing_decisions_and_unapproved_execution(tmp_path: Path) -> None:
    cid, play, service, dice = await setup(tmp_path, configured())
    await service.submit(cid, request(), authenticated_actor_id="a")
    with pytest.raises(ConflictError):
        await service.submit(cid, execution(revision=1), authenticated_actor_id="a")
    with pytest.raises(ConflictError):
        await service.submit(
            cid,
            request().model_copy(
                update={"action": request().action.model_copy(update={"target_id": "chest"})}
            ),
            authenticated_actor_id="a",
        )
    responses = await asyncio.gather(
        *(
            service.submit(
                cid,
                decision().model_copy(update={"id": f"decision-{i}"}),
                authenticated_actor_id="gm",
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, Ruling) for r in responses) == 1
    assert sum(isinstance(r, ConflictError) for r in responses) == 1
    assert len(await play.store.history(cid)) == 2 and dice.calls == 0


async def test_player_approval_can_execute_but_other_actors_cannot(tmp_path: Path) -> None:
    cid, play, service, dice = await setup(tmp_path, configured(player=True))
    await service.submit(cid, request(), authenticated_actor_id="a")
    await service.submit(cid, decision(actor_id="a"), authenticated_actor_id="a")
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="affected actor"):
        await service.submit(
            cid, execution().model_copy(update={"actor_id": "gm"}), authenticated_actor_id="gm"
        )
    assert await play.store.read(cid) == before
    result = await service.submit(cid, execution(), authenticated_actor_id="a")
    assert isinstance(result, ActionResult) and result.status == "committed" and dice.calls == 3


async def test_execution_revalidates_capability_and_exact_approved_mechanics(
    tmp_path: Path,
) -> None:
    cid, play, service, dice = await setup(tmp_path, configured())
    await service.submit(cid, request(), authenticated_actor_id="a")
    await service.submit(cid, decision(), authenticated_actor_id="gm")
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    action = Social(id="check", actor_id="a", expected_revision=2, target_id="b")
    for changed in (
        action.model_copy(update={"target_id": "chest"}),
        action.model_copy(update={"actor_id": "b"}),
        action.model_copy(update={"approach": "deception"}),
        action.model_copy(update={"hypothetical": True}),
    ):
        with pytest.raises(ValidationError, match="does not match"):
            play.engine.resolve(state, changed, ruling_id="ruling", rng=dice)
    unable = state.model_copy(
        update={"actors": (state.actors[0].model_copy(update={"conditions": ("stunned",)}),)}
    )
    unchanged, resolved_events = play.engine.resolve(unable, action, ruling_id="ruling", rng=dice)
    result = action_result(resolved_events)
    assert unchanged == unable and result.code == "actor.condition" and dice.calls == 0
    expired = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": 10, "scheduled": ()})}
    )
    with pytest.raises(ConflictError, match="current revision"):
        play.engine.resolve(expired, action, ruling_id="ruling", rng=dice)
    assert dice.calls == 0


async def test_forged_checkpoint_alternatives_pins_and_policy_authority_are_blocked(
    tmp_path: Path,
) -> None:
    cid, play, service, _ = await setup(tmp_path, configured(automatic=True))
    await service.submit(cid, request(), authenticated_actor_id="a")
    await service.submit(cid, decision(), authenticated_actor_id="gm")
    state = PlayState.model_validate_json((await play.store.read(cid))["play_json"])
    record = state.rulings[0]
    invalid = (
        record.model_copy(update={"policy_digest": "changed"}),
        record.model_copy(update={"configuration_digest": "changed"}),
        record.model_copy(update={"campaign_id": "another-campaign"}),
        record.model_copy(update={"expires_at": 10000}),
        record.model_copy(update={"status": "pending"}),
        record.model_copy(update={"alternatives": ()}),
        record.model_copy(update={"selected_id": "invented"}),
        record.model_copy(update={"approver_id": "unknown"}),
        record.model_copy(update={"authority": "policy", "approver_id": "system:adjudication"}),
        record.model_copy(update={"decided_revision": 1}),
        record.model_copy(update={"status": "executed", "executed_revision": 1}),
    )
    for forged in invalid:
        with pytest.raises(ValidationError):
            play.engine.validate(state.model_copy(update={"rulings": (forged,)}))
    with pytest.raises(ValidationError, match="Duplicate ruling"):
        play.engine.validate(state.model_copy(update={"rulings": (record, record)}))


async def test_request_rejects_noop_unavailable_unsupported_and_wrong_nested_identity(
    tmp_path: Path,
) -> None:
    cid, play, service, dice = await setup(tmp_path, configured())
    before = await play.store.read(cid)
    original = request()
    bad_actions = (
        original.action.model_copy(update={"actor_id": "b"}),
        original.action.model_copy(update={"expected_revision": 1}),
        original.action.model_copy(update={"hypothetical": True}),
        original.action.model_copy(update={"approach": "diplomacy"}),
        original.action.model_copy(update={"target_id": "hidden"}),
        original.action.model_copy(update={"target_id": "chest"}),
    )
    for action in bad_actions:
        with pytest.raises(ValidationError):
            await service.submit(
                cid, original.model_copy(update={"action": action}), authenticated_actor_id="a"
            )
    with pytest.raises(ValidationError, match="hypothetical"):
        await service.submit(
            cid, original.model_copy(update={"hypothetical": True}), authenticated_actor_id="a"
        )
    assert await play.store.read(cid) == before and dice.calls == 0


async def test_policy_is_disabled_by_default_and_cannot_be_silently_enabled(tmp_path: Path) -> None:
    cid, play, service, _ = await setup(tmp_path, engine())
    with pytest.raises(ValidationError, match="not configured"):
        await service.submit(cid, request(), authenticated_actor_id="a")
    new_service = AdjudicationService(PlayService(play.store, configured()))
    with pytest.raises(ValidationError, match="migration"):
        await new_service.submit(cid, request(), authenticated_actor_id="a")
    assert (await play.store.read(cid))["revision"] == 0
