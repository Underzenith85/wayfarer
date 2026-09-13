"""Characters B294-B296 independent expected-result fixtures for issue #500."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import seed_play
from test_actions import actor_setup, campaign, engine, resource_seed, world
from test_wave10 import prepare as prepare_recovery
from test_wave10 import setback

from wayfarer.engine.character.compiler import CharacterDraft
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.campaign.transformations import (
    AttachmentRoute,
    TraitRoute,
    TransformationRecord,
    TransformationRule,
    TransformationRules,
    visible_transformations,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.transformations import TransformationService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def target(st: int) -> CharacterDraft:
    original = actor_setup().proposal.draft
    return original.model_copy(
        update={
            "purchases": tuple(
                purchase.model_copy(update={"amount": st})
                if purchase.definition_id == "attribute:st"
                else purchase
                for purchase in original.purchases
            )
        }
    )


def trait_routes(*, st: Literal["mind", "body", "neither"] = "body") -> tuple[TraitRoute, ...]:
    return tuple(
        TraitRoute(
            definition_id=purchase.definition_id,
            follows=st if purchase.definition_id == "attribute:st" else "mind",
        )
        for purchase in actor_setup().proposal.draft.purchases
    )


def transfer_routes() -> tuple[TraitRoute, ...]:
    return tuple(
        TraitRoute(
            definition_id=purchase.definition_id,
            follows=(
                "body"
                if purchase.definition_id in {"attribute:st", "attribute:dx", "attribute:ht"}
                else "mind"
            ),
        )
        for purchase in actor_setup().proposal.draft.purchases
    )


def attachment_routes() -> tuple[AttachmentRoute, ...]:
    return tuple(
        AttachmentRoute(kind=kind, follows="mind")
        for kind in ("inventory", "credentials", "relationships", "knowledge", "control")
    )


async def setup(tmp_path: Path, rule: TransformationRule) -> tuple[str, PlayService]:
    base = engine()
    reducer = ActionEngine(
        base.reviewer,
        base.resources,
        base.rules.model_copy(
            update={
                "transformations": TransformationRules(
                    id="campaign-transformations", version=1, transformations=(rule,)
                )
            }
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "transformations.sqlite", 10), reducer)
    initial = campaign(reducer)
    await seed_play(play, initial, world(), resource_seed(), (actor_setup(),))
    return initial["id"], play


def body_rule(**changes: object) -> TransformationRule:
    values: dict[str, object] = {
        "id": "reinforced-body",
        "actor_id": "a",
        "kind": "body-modification",
        "source_ref": "B294",
        "target": target(11),
        "target_body_id": "a:reinforced",
        "trait_routes": trait_routes(),
        "attachment_routes": attachment_routes(),
        "point_policy": "adjust",
    }
    values.update(changes)
    return TransformationRule.model_validate(values)


async def propose(
    service: TransformationService,
    cid: str,
    build_revision: str,
    revision: int = 0,
    principal_id: str = "a",
) -> TransformationRecord:
    return await service.execute(
        cid,
        {
            "operation": "propose",
            "id": "proposal-command",
            "actor_id": "a",
            "expected_revision": revision,
            "expected_build_revision": build_revision,
            "rule_id": "reinforced-body",
        },
        principal_id=principal_id,
    )


async def test_treatment_approval_interrupt_and_restart_are_authoritative(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, body_rule(treatment_seconds=10, recovery_seconds=86_400))
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    proposed = await propose(service, cid, build.revision)
    with pytest.raises(ValidationError, match="director authority"):
        await service.execute(
            cid,
            {
                "operation": "approve",
                "id": "forged-approval",
                "actor_id": "a",
                "expected_revision": 1,
                "proposal_id": proposed.proposal_id,
                "reason": "self approved",
            },
            principal_id="a",
        )
    treatment = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "gm-approval",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Approved clinic procedure",
        },
        principal_id="gm",
    )
    assert treatment.status == "treatment" and treatment.ready_at == 10
    interrupted = await service.execute(
        cid,
        {
            "operation": "resolve",
            "id": "interrupt-treatment",
            "actor_id": "a",
            "expected_revision": 2,
            "proposal_id": proposed.proposal_id,
            "resolution": "interrupt",
        },
        principal_id="a",
    )
    assert interrupted.status == "interrupted"
    reopened = PlayService(play.store, play.engine)
    assert reopened._load(await reopened.store.read(cid)).transformations.records[-1] == interrupted
    assert await reopened.store.read(cid) == await reopened.store.replay(cid)


async def test_permanent_body_change_preserves_identity_inventory_and_injury_deficit(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, body_rule())
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    proposed = await propose(service, cid, build.revision)
    active = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "approve-change",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Approved permanent modification",
        },
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert active.status == "active" and active.target_body_id == "a:reinforced"
    assert state.actors[0].actor_id == "a" and state.actors[0].proposal.draft.name == "Mira"
    assert {item.owner_id for item in state.resources.items} == {"a"}
    assert state.advancement[-1].kind == "transformation"
    assert state.advancement[-1].character_point_delta == 10
    assert state.advancement[-1].points == 0
    assert visible_transformations(state.transformations, actor_ids=frozenset(), is_gm=False) == ()
    assert visible_transformations(
        state.transformations, actor_ids=frozenset({"a"}), is_gm=False
    ) == (active,)


async def test_temporary_affliction_expires_to_exact_previous_build(tmp_path: Path) -> None:
    rule = body_rule(
        kind="supernatural-affliction",
        source_ref="B296",
        voluntary=False,
        expires_after_seconds=5,
        curable=True,
    )
    cid, play = await setup(tmp_path, rule)
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    original = state.actors[0].proposal
    build = play.engine.reviewer.review(original).compilation.build
    assert build is not None
    proposed = await propose(service, cid, build.revision)
    active = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "afflict",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Recorded curse source",
        },
        principal_id="gm",
    )
    with pytest.raises(ValidationError, match="has not expired"):
        await service.execute(
            cid,
            {
                "operation": "resolve",
                "id": "early-expiry",
                "actor_id": "a",
                "expected_revision": 2,
                "proposal_id": active.proposal_id,
                "resolution": "expire",
            },
            principal_id="a",
        )
    await play.execute(
        cid,
        Wait(id="wait-expiry", actor_id="a", expected_revision=2, ticks=5),
        principal_id="a",
    )
    reverted = await service.execute(
        cid,
        {
            "operation": "resolve",
            "id": "expire-affliction",
            "actor_id": "a",
            "expected_revision": 3,
            "proposal_id": active.proposal_id,
            "resolution": "expire",
        },
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert reverted.status == "reverted" and state.actors[0].proposal == original
    assert state.advancement[-1].character_point_delta == -10
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_reversible_body_change_restores_the_prior_approved_build(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, body_rule(reversible=True))
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    original = state.actors[0].proposal
    build = play.engine.reviewer.review(original).compilation.build
    assert build is not None
    proposed = await propose(service, cid, build.revision)
    active = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "install-modification",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Approved reversible implant",
        },
        principal_id="gm",
    )
    reverted = await service.execute(
        cid,
        {
            "operation": "resolve",
            "id": "remove-modification",
            "actor_id": "a",
            "expected_revision": 2,
            "proposal_id": active.proposal_id,
            "resolution": "reverse",
        },
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert reverted.status == "reverted" and state.actors[0].proposal == original
    assert [entry.character_point_delta for entry in state.advancement] == [10, -10]


async def test_mind_transfer_uses_asymmetric_trait_mapping(tmp_path: Path) -> None:
    rule = body_rule(
        kind="mind-transfer",
        source_ref="B296",
        trait_routes=transfer_routes(),
        target_body_id="manufactured-body",
    )
    cid, play = await setup(tmp_path, rule)
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    proposed = await propose(service, cid, build.revision)
    active = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "approve-transfer",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Approved mind transfer",
        },
        principal_id="gm",
    )
    routes = {route.definition_id: route.follows for route in active.trait_routes}
    assert active.target_body_id == "manufactured-body"
    assert routes["attribute:iq"] == routes["skill:observation"] == "mind"
    assert routes["attribute:st"] == routes["attribute:dx"] == routes["attribute:ht"] == "body"


async def test_missing_campaign_permission_and_incomplete_mapping_fail_closed(
    tmp_path: Path,
) -> None:
    base = engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "disabled.sqlite", 10), base)
    initial = campaign(base)
    await seed_play(play, initial, world(), resource_seed(), (actor_setup(),))
    state = play._load(await play.store.read(initial["id"]))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    with pytest.raises(ValidationError, match="unavailable"):
        await propose(TransformationService(play), initial["id"], build.revision)

    cid, enabled = await setup(
        tmp_path,
        body_rule(trait_routes=trait_routes()[:-1]),
    )
    state = enabled._load(await enabled.store.read(cid))
    build = enabled.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    with pytest.raises(ValidationError, match="explicit mapping"):
        await propose(TransformationService(enabled), cid, build.revision)


async def test_mind_transfer_rejects_iq_mapped_to_the_body(tmp_path: Path) -> None:
    routes = tuple(
        route.model_copy(update={"follows": "body"})
        if route.definition_id
        in {
            "attribute:st",
            "attribute:dx",
            "attribute:iq",
            "attribute:ht",
        }
        else route
        for route in trait_routes()
    )
    rule = body_rule(kind="mind-transfer", source_ref="B296", trait_routes=routes)
    cid, play = await setup(tmp_path, rule)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    with pytest.raises(ValidationError, match="preserve IQ"):
        await propose(TransformationService(play), cid, build.revision)


async def test_death_transformation_requires_death_and_does_not_replace_identity(
    tmp_path: Path,
) -> None:
    rule = body_rule(
        kind="death-transformation",
        source_ref="B296",
        requires_death=True,
        trait_routes=transfer_routes(),
        target_body_id="a:returned-form",
    )
    rules = TransformationRules(id="returns", version=1, transformations=(rule,))
    cid, play = await prepare_recovery(tmp_path, transformations=rules)
    service = TransformationService(play)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None
    with pytest.raises(ValidationError, match="death prerequisite"):
        await propose(service, cid, build.revision, principal_id="alice")

    await setback(cid, play, "death")
    state = play._load(await play.store.read(cid))
    proposed = await propose(
        service, cid, build.revision, revision=state.revision, principal_id="alice"
    )
    active = await service.execute(
        cid,
        {
            "operation": "approve",
            "id": "approve-return",
            "actor_id": "a",
            "expected_revision": state.revision + 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Authored resurrection path",
        },
        principal_id="gm",
    )
    final = play._load(await play.store.read(cid))
    assert active.status == "active" and "a" not in final.recovery.dead_actor_ids
    assert final.actors[0].actor_id == "a" and final.actors[0].proposal.draft.name == "Mira"
    assert "unconscious" in final.actors[0].conditions  # no implicit healing
