"""Captured damage uses its launched source while later actor authority stays current."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_gurps_maneuvers import turn
from test_opponent_attack_host import begin as begin_attack
from test_opponent_attack_inventory import fixture
from test_owner_damage_host import begin as begin_damage

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.errors import ValidationError
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamageOutcome
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


def changed_attacker(play: PlayService, state: PlayState, *, revoked: bool) -> PlayState:
    actor = next(a for a in state.actors if a.actor_id == "a")
    proposal = actor.proposal.model_copy(
        update={
            "draft": actor.proposal.draft.model_copy(
                update={
                    "purchases": tuple(
                        p.model_copy(update={"amount": 15})
                        if p.definition_id == "attribute:st"
                        else p
                        for p in actor.proposal.draft.purchases
                        if p.definition_id != "trait:advantage:luck"
                    )
                }
            )
        }
    )
    approval = (
        None
        if revoked
        else play.engine.reviewer.approve(
            proposal,
            campaign_id=state.campaign_id,
            actor_id="a",
            revision=state.revision,
            approver_id="gm",
            reason="Current source authority regression",
        )
    )
    return state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"proposal": proposal, "approval": approval})
                if a.actor_id == "a"
                else a
                for a in state.actors
            ),
            "approvals": state.approvals + ((approval,) if approval else ()),
            "resources": state.resources.model_copy(
                update={
                    "owners": tuple(
                        owner.model_copy(
                            update={
                                "definitions": tuple(
                                    d for d in owner.definitions if d != "trait:advantage:luck"
                                )
                            }
                        )
                        if owner.actor_id == "a"
                        else owner
                        for owner in state.resources.owners
                    ),
                    "pools": tuple(
                        p.model_copy(
                            update={
                                "current": 8 if p.id == "hp:a" else 7,
                                "maximum": 15 if p.id == "hp:a" else p.maximum,
                            }
                        )
                        if p.id in ("hp:a", "fp:a")
                        else p
                        for p in state.resources.pools
                    ),
                }
            ),
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
@pytest.mark.parametrize("revoked", [False, True])
async def test_launched_source_survives_changed_approval_and_retains_current_body(
    tmp_path: Path, backend: str, ranged: bool, revoked: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=ranged)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged" if ranged else "swing",
    )
    play.rng = RecordedDice((3, 3, 3))
    _, original = await begin_attack(play, cid, prepare_owner_damage=True)
    assert original.pending_id and original.check
    await change(play, cid, lambda state: changed_attacker(play, state, revoked=revoked))
    state = play._load(await play.store.read(cid))
    current_actor = next(a for a in state.actors if a.actor_id == "a")
    response = ChooseDefense(
        id="close-attack",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    command = ChooseOpponentAttack(
        id=response.id,
        actor_id="b",
        expected_revision=state.revision,
        pending_id=original.pending_id,
        choice="accept",
        response=response,
    )
    play.rng = RecordedDice((1,))
    selected = await TaskService(play).execute(cid, command, principal_id="b")
    assert selected.status == "completed" and selected.pending_id is None
    assert selected.check == original.check and selected.damage_json is None
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending) and pending.original == (1,)
    assert pending.captured_attacker.statistics and pending.captured_attacker.statistics.st == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    choose = ChooseOwnerDamage(
        id="finish-damage",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    before = await restarted.store.read(cid)
    with pytest.raises(ValidationError):
        await TaskService(restarted).execute(cid, choose, principal_id="a")
    assert await restarted.store.read(cid) == before
    choose = choose.model_copy(update={"choice": "accept"})
    result = await TaskService(restarted).execute(cid, choose, principal_id="gm")
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.combat and outcome.combat.injury
    assert outcome.combat.injury.attack == original.check
    assert outcome.combat.injury.hp_after == (9 if ranged else 7)
    after = restarted._load(await restarted.store.read(cid))
    assert next(a for a in after.actors if a.actor_id == "a") == current_actor
    assert [(p.id, p.current) for p in after.resources.pools if p.id in ("hp:a", "fp:a")] == [
        (p.id, p.current) for p in state.resources.pools if p.id in ("hp:a", "fp:a")
    ]
    assert snapshot(after).pending is None and not real_play_clock(after).cooldowns
    assert await TaskService(restarted).execute(cid, choose, principal_id="gm") == result
    assert restarted._load(await restarted.store.read(cid)) == after
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    if revoked:
        with pytest.raises(ValidationError, match="approval"):
            build(restarted.rules_context, after, "a")
    else:
        current_build = build(restarted.rules_context, after, "a")
        assert current_build.statistics and current_build.statistics.st == 15
        if not ranged:
            await turn(cid, restarted, "b", "do_nothing")
            await turn(
                cid, restarted, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing"
            )
            restarted.rng = RecordedDice((3, 3, 3, 1, 1))
            await begin_damage(restarted, cid, identifier="new-body-damage", principal="b")
            current_pending = snapshot(restarted._load(await restarted.store.read(cid))).pending
            assert isinstance(current_pending, InventoryDamagePending)
            assert current_pending.preparation.dice_count == 2 and current_pending.original == (
                1,
                1,
            )
            assert restarted.rng.exhausted()
