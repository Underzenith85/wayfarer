"""Closed delivery and dice survive current target changes before injury is rolled."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_gurps_maneuvers import turn
from test_opponent_attack_inventory import fixture as inventory_fixture
from test_owner_damage_host import begin
from test_owner_damage_host import fixture as composed_fixture
from test_owner_damage_missile import fixture as missile_fixture

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    OwnerDamagePending,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


def current_target(play: PlayService, state: PlayState, *, ht: int = 20) -> PlayState:
    actor = next(a for a in state.actors if a.actor_id == "b")
    proposal = actor.proposal.model_copy(
        update={
            "draft": actor.proposal.draft.model_copy(
                update={
                    "purchases": tuple(
                        p.model_copy(update={"amount": ht})
                        if p.definition_id == "attribute:ht"
                        else p
                        for p in actor.proposal.draft.purchases
                    )
                    + (Purchase(definition_id="secondary:basic-speed", amount=20),)
                }
            )
        }
    )
    approval = play.engine.reviewer.approve(
        proposal,
        campaign_id=state.campaign_id,
        actor_id="b",
        revision=state.revision,
        approver_id="gm",
        reason="Current unresolved injury uses current approved target HT",
    )
    encounter = state.encounters[0]
    defender = next(p for p in encounter.participants if p.actor_id == "b")
    encounter = CombatEngine._replace(
        encounter, defender.model_copy(update={"posture": "kneeling"})
    )
    return state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"proposal": proposal, "approval": approval})
                if a.actor_id == "b"
                else a
                for a in state.actors
            ),
            "approvals": state.approvals + (approval,),
            "encounters": (encounter,),
            "resources": state.resources.model_copy(
                update={
                    "owners": tuple(
                        o.model_copy(
                            update={"definitions": o.definitions + ("secondary:basic-speed",)}
                        )
                        if o.actor_id == "b"
                        else o
                        for o in state.resources.owners
                    ),
                    "pools": tuple(
                        p.model_copy(update={"current": 8})
                        if p.id == "hp:b"
                        else p.model_copy(update={"maximum": ht, "current": 7})
                        if p.id == "fp:b"
                        else p
                        for p in state.resources.pools
                    ),
                }
            ),
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ht,check,stunned", [(20, (4, 4, 4), False), (8, (3, 3, 3), True)])
@pytest.mark.parametrize("route", ["composed", "melee", "ranged", "missile"])
async def test_changed_approved_target_finishes_with_live_ht_hp_fp_and_posture(
    tmp_path: Path, backend: str, route: str, ht: int, check: tuple[int, int, int], stunned: bool
) -> None:
    original: tuple[int, ...]
    rerolls: tuple[int, ...]
    if route == "composed":
        cid, play = await composed_fixture(tmp_path, backend)
        original = (2, 2, 2, 1, 1)
        rerolls = (2, 2, 3, 3)
    elif route == "missile":
        cid, play = await missile_fixture(tmp_path, backend)
        original = (1, 2, 2, 1)
        rerolls = (3, 6)
    else:
        cid, play = await inventory_fixture(tmp_path, backend, ranged=route == "ranged")
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged" if route == "ranged" else "swing",
        )
        original = (3, 3, 3, 1)
        rerolls = (3, 6) if route == "ranged" else (3, 4)
    play.rng = RecordedDice(original)
    await begin(play, cid, principal="b")
    captured = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(captured, (InventoryDamagePending, OwnerDamagePending))
    await change(play, cid, lambda state: current_target(play, state, ht=ht))
    changed = play._load(await play.store.read(cid))
    target_actor = next(a for a in changed.actors if a.actor_id == "b")
    caster_fp = next(p.current for p in changed.resources.pools if p.id == "fp:a")
    restarted = build_play(
        tmp_path,
        play.engine,
        backend=backend,
        rng=RecordedDice(rerolls + check),
        instants=play.instants,
    )
    command = ChooseOwnerDamage(
        id="current-injury",
        actor_id="a",
        expected_revision=changed.revision,
        pending_id=captured.id,
        choice="use-luck",
    )
    result = await TaskService(restarted).execute(cid, command, principal_id="a")
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.combat and outcome.combat.injury
    injury = outcome.combat.injury
    assert injury.attack.dice == original[:3] and injury.hp_before == 8
    assert injury.injury == (7 if route == "melee" else 6)
    assert injury.hp_after == (1 if route == "melee" else 2)
    after = restarted._load(await restarted.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.injury and hp.injury.stunned == stunned and hp.injury.prone == stunned
    assert next(p.current for p in after.resources.pools if p.id == "fp:b") == 7
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == caster_fp
    assert next(p.posture for p in after.encounters[0].participants if p.actor_id == "b") == (
        "prone" if stunned else "kneeling"
    )
    after_actor = next(a for a in after.actors if a.actor_id == "b")
    assert (after_actor.proposal, after_actor.approval, after_actor.body) == (
        target_actor.proposal,
        target_actor.approval,
        target_actor.body,
    )
    assert snapshot(after).pending is None and len(snapshot(after).luck.receipts) == 1
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert restarted._load(await restarted.store.read(cid)) == after


async def test_changed_delivery_identity_cannot_spend_or_apply_another_attacks_damage(
    tmp_path: Path,
) -> None:
    from wayfarer.errors import ConflictError

    cid, play = await inventory_fixture(tmp_path, "sqlite", ranged=False)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((3, 3, 3, 1))
    await begin(play, cid, principal="b")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, InventoryDamagePending)

    def another(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        defense = encounter.pending_defense
        assert defense is not None
        return state.model_copy(
            update={
                "encounters": (
                    encounter.model_copy(
                        update={
                            "pending_defense": defense.model_copy(update={"id": "another-attack"}),
                        }
                    ),
                )
            }
        )

    await change(play, cid, another)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="delivery identity"):
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="wrong-delivery",
                actor_id="a",
                expected_revision=play._load(before).revision,
                pending_id=pending.id,
                choice="use-luck",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize(
    "route,original,injury",
    [
        ("composed", (2, 2, 2, 2, 3), 3),
        ("melee", (3, 3, 3, 4), 4),
        ("ranged", (3, 3, 3, 5), 3),
    ],
)
async def test_original_acceptance_uses_current_armor_without_reroll_or_luck(
    tmp_path: Path, route: str, original: tuple[int, ...], injury: int
) -> None:
    from wayfarer.engine.simulation.resources import Item

    if route == "composed":
        cid, play = await composed_fixture(tmp_path, "sqlite")
    else:
        cid, play = await inventory_fixture(tmp_path, "sqlite", ranged=route == "ranged")
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged" if route == "ranged" else "swing",
        )
    play.rng = RecordedDice(original)
    await begin(play, cid, principal="b")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert isinstance(pending, (InventoryDamagePending, OwnerDamagePending))

    def equipped(state: PlayState) -> PlayState:
        state = current_target(play, state)
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": state.resources.items
                        + (
                            Item(
                                id="new-leather",
                                definition_id="equipment:leather-armor",
                                owner_id="b",
                                equipped=True,
                            ),
                        ),
                    }
                )
            }
        )

    await change(play, cid, equipped)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    result = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="accept-live-armor",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="a",
    )
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.combat and outcome.combat.injury
    actual = outcome.combat.injury
    assert actual.damage_dice == original[3:] and actual.attack.dice == original[:3]
    assert actual.resistance == 2 and actual.injury == injury
    assert actual.hp_before == 8 and actual.hp_after == 8 - injury
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "fp:b") == 7
    assert (
        next(p.posture for p in after.encounters[0].participants if p.actor_id == "b") == "kneeling"
    )
    assert not snapshot(after).luck.receipts and snapshot(after).pending is None
    assert play.rng.exhausted()
