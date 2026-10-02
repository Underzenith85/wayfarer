"""B66 weapon damage changes actual cutting injury through ordinary combat settlement."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_gurps_maneuvers import turn
from test_opponent_attack_inventory import fixture
from test_owner_damage_host import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_selected_melee_damage_commits_actual_wound_turn_and_exact_retry(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=False)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3) + (() if secret else (1,)))
    preparation_command, _ = await begin(
        play, cid, secret=secret, principal="gm" if secret else "b"
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    assert pending.original == (None if secret else (1,))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert (
        state.encounters[0].current_actor_id == "a"
        and state.resources.game_time == before.resources.game_time
    )
    assert not state.encounters[0].wounds and play.rng.exhausted()
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="b")
    view = await TaskService(play).pending(cid, principal_id="a")
    assert view and (view.damage_json is None) == secret
    command = ChooseOwnerDamage(
        id="select-weapon-damage",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(((1,) if secret else ()) + (3, 5, 4, 4, 4))
    result = await TaskService(play).execute(cid, command, principal_id="a")
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    # Source 1d+1 cutting: selected5+1=6basic, x1.5=9injury.
    assert (
        hp.current == 1
        and hp.injury
        and hp.injury.shock == 4
        and hp.injury.stunned
        and hp.injury.prone
    )
    assert (
        after.encounters[0].current_actor_id == "b" and after.encounters[0].pending_defense is None
    )
    assert len(after.encounters[0].wounds) == 1 and play.rng.exhausted()
    wound = after.encounters[0].wounds[0]
    assert wound.attack.dice == (3, 3, 3) and wound.damage_dice == (5,) and wound.basic_damage == 6
    assert len(snapshot(after).luck.receipts) == 1 and snapshot(after).pending is None
    if secret:
        assert result.damage_json is None and result.luck is None
    else:
        assert result.damage_json and OwnerDamageOutcome.model_validate_json(
            result.damage_json
        ).dice == (5,)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    await TaskService(restarted).execute(
        cid, preparation_command, principal_id="gm" if secret else "b"
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)
    with pytest.raises(ConflictError):
        await TaskService(restarted).execute(
            cid,
            command.model_copy(update={"id": "late", "expected_revision": after.revision}),
            principal_id="a",
        )


async def test_all_out_double_keeps_wound_order_and_advances_only_after_second_hit(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path, "sqlite", ranged=False)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="double",
    )
    for index, original in enumerate((1, 2)):
        play.rng = RecordedDice((3, 3, 3, original))
        await begin(play, cid, identifier="damage-" + str(index), principal="b")
        state = play._load(await play.store.read(cid))
        pending = snapshot(state).pending
        assert isinstance(pending, InventoryDamagePending)
        play.rng = RecordedDice(())
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="accept-" + str(index),
                actor_id="a",
                expected_revision=state.revision,
                pending_id=pending.id,
                choice="accept",
            ),
            principal_id="a",
        )
        after = play._load(await play.store.read(cid))
        assert after.encounters[0].current_actor_id == ("a" if index == 0 else "b")
        assert (after.encounters[0].pending_defense is not None) == (index == 0)
        assert len(after.encounters[0].wounds) == index + 1
        assert play.rng.exhausted()
    assert [w.damage_dice for w in after.encounters[0].wounds] == [(1,), (2,)]
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 3


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_inventory_damage_full_event_fold_and_seed_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=False)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "01" * 32
    await begin(play, cid, principal="b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="select",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    records = (await play.store.history(cid))[count:]
    identifiers = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "replayed"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


async def test_owner_cannot_choose_the_other_actors_actual_weapon_defense(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, "sqlite", ranged=False)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await play.store.read(cid)
    state = play._load(before)
    response = ChooseDefense(
        id="forged",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    command = PrepareOwnerDamage(
        id=response.id, actor_id="a", expected_revision=state.revision, response=response
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Defense|defense"):
        await TaskService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()
