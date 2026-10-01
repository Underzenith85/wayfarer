"""A saved survival plan never preserves revoked campaign control."""

from pathlib import Path

import pytest
from test_consumption_purchases import due_service

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.health.survival import SettleSurvival
from wayfarer.errors import AuthorizationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.survival import SurvivalEnvironment, SurvivalService


async def revoke_control(play: PlayService, cid: str, revision: int) -> None:
    def resolve(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        updated = state.model_copy(
            update={
                "revision": revision + 1,
                "resources": state.resources.model_copy(update={"revision": revision + 1}),
                "members": tuple(
                    member.model_copy(update={"role": "spectator", "actor_ids": ()})
                    if member.principal_id == "owner"
                    else member
                    for member in state.members
                ),
            }
        )
        play.commit(campaign, updated)
        return CommandReceipt(action="v1-membership", outcome="revoked")

    await play.store.commit_turn(cid, "revoke", revision, "revoke", resolve, actor_id="gm")


async def test_saved_plan_future_revision_cannot_spend_after_control_revocation(
    tmp_path: Path,
) -> None:
    play, initial = await due_service(tmp_path, principal_id="owner")
    state = play._load(await play.store.read(initial["id"]))
    command = SettleSurvival(id="future", actor_id="a", expected_revision=1)
    plan = SurvivalService(play, lambda *_: SurvivalEnvironment()).plan(
        play, command, member_for(state, "owner")
    )
    await revoke_control(play, initial["id"], 0)
    before = await play.store.read(initial["id"])
    with pytest.raises(AuthorizationError):
        await submit(play, initial["id"], plan, principal_id="owner")
    assert await play.store.read(initial["id"]) == before
    assert (
        next(pool for pool in play._load(before).resources.pools if pool.id == "fp:a").current == 10
    )


async def test_saved_duplicate_plan_cannot_disclose_result_after_control_revocation(
    tmp_path: Path,
) -> None:
    play, initial = await due_service(tmp_path, principal_id="owner")
    state = play._load(await play.store.read(initial["id"]))
    command = SettleSurvival(id="meal", actor_id="a", expected_revision=0)
    service = SurvivalService(play, lambda *_: SurvivalEnvironment())
    plan = service.plan(play, command, member_for(state, "owner"))
    result = await service.execute(initial["id"], command, principal_id="owner")
    assert result.fp_lost == 1
    await revoke_control(play, initial["id"], 1)
    before = await play.store.read(initial["id"])
    with pytest.raises(AuthorizationError):
        await submit(play, initial["id"], plan, principal_id="owner")
    assert await play.store.read(initial["id"]) == before
