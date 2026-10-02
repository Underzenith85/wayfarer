"""A cancelled private attack cannot make later damage preparation reveal dice."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_gurps_maneuvers import turn
from test_opponent_attack_inventory import fixture as inventory_fixture
from test_opponent_attack_secret import choose_secret
from test_opponent_attack_secret import prepare as secret_begin
from test_owner_damage_host import begin, fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamagePending
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("route", ["composed", "melee", "ranged"])
async def test_direct_damage_inherits_cancelled_attack_secrecy_across_restart(
    tmp_path: Path, backend: str, route: str
) -> None:
    if route == "composed":
        cid, play = await fixture(tmp_path, backend)
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
    _, secret = await secret_begin(play, cid)
    await choose_secret(play, cid, secret.pending_id, choice="cancel", principal="gm")
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice((2, 2, 2)), instants=play.instants
    )
    command, response = await begin(restarted, cid, principal="b", secret=False)
    assert not command.secret and response.secret and response.damage_json is None
    state = restarted._load(await restarted.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, (OwnerDamagePending, InventoryDamagePending))
    assert pending.secret and pending.original is None
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    shown = await TaskService(restarted).pending(cid, principal_id="a")
    assert shown and shown.pending_id == pending.id and shown.damage_json is None
    restarted.rng = RecordedDice((1, 1) if route == "composed" else (1,))
    result = await TaskService(restarted).execute(
        cid,
        ChooseOwnerDamage(
            id="accept-secret",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="gm",
    )
    assert result.secret and result.damage_json
    after = restarted._load(await restarted.store.read(cid))
    assert (
        next(p.current for p in after.resources.pools if p.id == "hp:b")
        == {
            "composed": 8,
            "melee": 7,
            "ranged": 9,
        }[route]
    )
    assert not snapshot(after).pending and restarted.rng.exhausted()
    assert all(
        not t.totals and not t.targets
        for t in after.encounters[0].tactical_traces
        if t.command_id == command.id
    )
    exact = await TaskService(restarted).execute(cid, command, principal_id="b")
    assert exact == response and exact.damage_json is None
    assert restarted._load(await restarted.store.read(cid)) == after
