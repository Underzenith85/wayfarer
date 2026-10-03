"""Actual typed owner-damage preparation settles the selected protector injury."""

from pathlib import Path

import pytest
from test_sacrificial_fireball_fixture import pending_fireball

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_task_producer_captures_protector_and_consumes_spell_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, response = await pending_fireball(tmp_path, backend, luck=True)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    response = response.model_copy(update={"expected_revision": state.revision})
    command = PrepareOwnerDamage(
        id=response.id,
        actor_id=response.actor_id,
        expected_revision=response.expected_revision,
        response=response,
    )
    play.rng = RecordedDice([2, 2, 2, 6])
    await task.execute(cid, command, principal_id="c")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending) and pending.actor_id == "a"
    assert latest(state.resources)["cast"].phase == "active"
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 20
    play.rng = RecordedDice([])
    choice = ChooseOwnerDamage(
        id="accept",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="accept",
    )
    result = await task.execute(cid, choice, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 18
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert latest(state.resources)["cast"].phase == "ended"
    assert await task.execute(cid, choice, principal_id="a") == result
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_staged_interposition_refreshes_current_armor_and_hp(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt
    from wayfarer.engine.simulation.health.injury import Wound, apply_injury
    from wayfarer.engine.simulation.resources import Unequip

    cid, play, response = await pending_fireball(tmp_path, backend, luck=True)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    response = response.model_copy(update={"expected_revision": state.revision})
    play.rng = RecordedDice([2, 2, 2, 6])
    await task.execute(
        cid,
        PrepareOwnerDamage(
            id=response.id, actor_id="c", expected_revision=state.revision, response=response
        ),
        principal_id="c",
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    resources = play.engine.resources.apply(
        state.resources,
        Unequip(
            id="remove-armor", actor_id="c", expected_revision=state.revision, item_id="target-0"
        ),
    )
    resources, _ = apply_injury(
        resources,
        Wound(
            id="current-wound",
            actor_id="c",
            expected_revision=resources.revision,
            basic_damage=1,
            resistance=0,
            damage_type="burn",
        ),
        ht=8,
        system=True,
        rng=RecordedDice([]),
    )
    encounter = state.encounters[0].model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"ready_item_ids": ()}) if p.actor_id == "c" else p
                for p in state.encounters[0].participants
            )
        }
    )
    updated = state.model_copy(
        update={"resources": resources, "revision": resources.revision, "encounters": (encounter,)}
    )

    def install(campaign: Campaign) -> CommandReceipt:
        play.engine.validate(updated)
        campaign["play_json"], campaign["revision"] = updated.model_dump_json(), updated.revision
        return CommandReceipt(action="resource", outcome="admitted-unequip-fixture")

    await play.store.commit_turn(cid, "remove-armor", state.revision, "remove-armor", install)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([])
    await task.execute(
        cid,
        ChooseOwnerDamage(
            id="accept",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == 13
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
