"""Campaigns fourth printing B480/B482: actual item casting costs and timing."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_enchanting_projects import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import MagicItemBinding, ManaLevel
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.magic.bindings import SpellChannel
from wayfarer.engine.simulation.magic.spells import SpellCommand, latest
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService


async def item_campaign(
    path: Path,
    backend: str,
    *,
    power: int,
    mana: ManaLevel,
    reduction: int,
    nonmage: bool = False,
    other_mage_only: bool = False,
) -> tuple[str, PlayService]:
    original, _, state = setup(path, hold_target=True)
    rules = original.rules.spells
    assert rules is not None
    rules = rules.model_copy(
        update={
            "channels": (
                SpellChannel(
                    id="item-daze",
                    actor_id="a",
                    target_id="b",
                    location_id="forge",
                    spell_id="daze",
                    magic_item_id="blade",
                    mana=mana,
                ),
            ),
            "magic_items": (
                MagicItemBinding(
                    id="found-daze-item",
                    item_id="blade",
                    spell_id="daze",
                    power=power,
                    power_reduction=reduction,
                ),
                *(
                    (
                        MagicItemBinding(
                            id="found-fireball-item",
                            item_id="blade",
                            spell_id="fireball",
                            power=20,
                            requires_magery=True,
                        ),
                    )
                    if other_mage_only
                    else ()
                ),
            ),
        }
    )
    engine = ActionEngine(
        original.reviewer, original.resources, original.rules.model_copy(update={"spells": rules})
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        replace(
            state.world,
            facts=state.world.facts + (Fact("visible-b", "b", "visible", "yes"),),
            knowledge=state.world.knowledge + (("a", "visible-b"),),
        ),
        state.resources,
        tuple(
            ActorSetup(
                actor_id=actor.actor_id,
                proposal=actor.proposal.model_copy(
                    update={
                        "draft": actor.proposal.draft.model_copy(
                            update={
                                "purchases": tuple(
                                    p
                                    for p in actor.proposal.draft.purchases
                                    if p.definition_id.startswith("attribute:")
                                )
                            }
                        )
                    }
                )
                if nonmage and actor.actor_id == "a"
                else actor.proposal,
            )
            for actor in state.actors
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("power", "mana", "reduction", "skill", "cost", "maintenance"),
    [
        (25, "normal", 0, 25, 3, 2),
        (20, "low", 0, 15, 3, 2),
        (20, "high", 0, 20, 3, 2),
        (25, "normal", 1, 25, 2, 1),
        (20, "low", 1, 15, 3, 2),
        (20, "high", 1, 20, 1, 0),
    ],
)
async def test_b482_item_power_has_one_mana_penalty_and_no_personal_discounts(
    tmp_path: Path,
    backend: str,
    power: int,
    mana: ManaLevel,
    reduction: int,
    skill: int,
    cost: int,
    maintenance: int,
) -> None:
    cid, play = await item_campaign(tmp_path, backend, power=power, mana=mana, reduction=reduction)
    command = SpellCommand(
        id="start-item",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="daze",
        channel_id="item-daze",
        cast_id="item-cast",
    )
    before = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before
    result = await SpellService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["item-cast"]
    assert (effect.skill, effect.cost, effect.maintenance) == (skill, cost, maintenance)
    assert effect.ready_at == 2  # Daze's listed time; Power25 cannot shorten it.
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await SpellService(restarted).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)

    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, command.model_copy(update={"id": "stale"}), principal_id="a"
        )
    await play.execute(
        cid, Wait(id="one", actor_id="a", expected_revision=1, ticks=1), principal_id="a"
    )
    await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "concentrate", "expected_revision": 2, "kind": "concentrate"}
        ),
        principal_id="a",
    )
    await play.execute(
        cid, Wait(id="two", actor_id="a", expected_revision=3, ticks=1), principal_id="a"
    )
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    completion = command.model_copy(
        update={"id": "complete", "expected_revision": 4, "kind": "complete"}
    )
    done = await SpellService(play).execute(cid, completion, principal_id="a")
    assert done.energy_spent == cost
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10 - cost
    assert latest(after.resources)["item-cast"].phase == "active"
    assert await SpellService(restarted).execute(cid, completion, principal_id="a") == done
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("other_mage_only", [False, True])
async def test_b482_mage_restriction_applies_to_the_whole_item(
    tmp_path: Path, backend: str, other_mage_only: bool
) -> None:
    cid, play = await item_campaign(
        tmp_path,
        backend,
        power=20,
        mana="normal",
        reduction=0,
        nonmage=True,
        other_mage_only=other_mage_only,
    )
    command = SpellCommand(
        id="nonmage-item",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="daze",
        channel_id="item-daze",
        cast_id="nonmage-cast",
    )
    before = await play.store.read(cid)
    if other_mage_only:
        with pytest.raises(ValidationError, match="requires Magery"):
            await SpellService(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    else:
        await SpellService(play).execute(cid, command, principal_id="a")
        after = play._load(await play.store.read(cid))
        assert latest(after.resources)["nonmage-cast"].cost == 3
        assert latest(after.resources)["nonmage-cast"].ready_at == 2
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
