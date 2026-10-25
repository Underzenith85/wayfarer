"""B551 launch policy survives actual Wait, original-roll and owner-damage stages."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_composed_attack_host import declare as declare_composed
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon
from test_opponent_attack_host import begin, choose
from test_opponent_attack_host import fixture as luck_composed
from test_opponent_attack_inventory import fixture as luck_inventory
from test_ranged_target_postures import consequence_seed, ready, response, shoot
from test_retreat_postures import battlefield

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ValidationError
from wayfarer.orchestration import ranged_target_posture_generations as generations
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_typed_original_roll_preserves_source_target_and_seed_reexecutes(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await ready(tmp_path, backend, "throw")
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, consequence_seed
    await shoot(cid, play)
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=incoming.id,
        ),
        principal_id="gm",
    )
    assert opened.pending_id and opened.check
    assert opened.check.total == 12
    assert opened.check.effective_target == (11 if enabled else 13)
    selected = response(play, await play.store.read(cid))
    command = ChooseOpponentAttack(
        id=selected.id,
        actor_id="b",
        expected_revision=selected.expected_revision,
        pending_id=opened.pending_id,
        choice="accept",
        response=selected,
    )
    chosen = await task.execute(cid, command, principal_id="b")
    assert chosen.combat_json
    result = CombatResult.model_validate_json(chosen.combat_json)
    assert result.injury and result.injury.attack == opened.check
    assert result.injury.injury == (0 if enabled else 3)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "typed-seed"),
    )
    assert len(checks) == 3 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final
    play.rng = RecordedDice([])
    assert await task.execute(cid, command, principal_id="b") == chosen


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_owner_damage_keeps_the_already_rolled_source_posture_target(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await ready(tmp_path, backend, "gun")
    await shoot(cid, play)
    task = TaskService(play)
    selected = response(play, await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=selected.expected_revision, running=True
        ),
        principal_id="gm",
    )
    selected = response(play, await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3, 3])
    preparation = PrepareOwnerDamage(
        id=selected.id,
        actor_id="b",
        expected_revision=selected.expected_revision,
        response=selected,
    )
    await task.execute(cid, preparation, principal_id="b")
    staged = play._load(await play.store.read(cid))
    pending = snapshot(staged).pending
    assert isinstance(pending, InventoryDamagePending)
    assert next(pool.current for pool in staged.resources.pools if pool.id == "hp:b") == 10
    monkeypatch.setattr(generations, "ACTIVE", False)
    play.rng = RecordedDice([])
    await task.execute(
        cid,
        ChooseOwnerDamage(
            id="accept",
            actor_id="a",
            expected_revision=staged.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="a",
    )
    final = play._load(await play.store.read(cid))
    trace = final.encounters[0].wounds[-1]
    assert trace.attack.effective_target == 11 and trace.attack.total == 9
    assert trace.injury == 3
    assert next(pool.current for pool in final.resources.pools if pool.id == "hp:b") == 7
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_wait_pins_target_rule_before_a_fresh_trigger(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_fixture=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
        battlefield=battlefield(),
        aware_of=("a", "b"),
        scene_bound=True,
        runtime_world=world().learn("a", "promise"),
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        ),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing", crouch="before")
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, consequence_seed
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "move",
            "reaction": "attack",
            "item_id": "sword-a",
            "mode_id": "ranged",
            "reaction_target_id": "b",
        },
    )
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    paused = await turn(cid, play, "b", "move", hex_path=(Hex(q=2, r=0),))
    assert paused.code == "combat.wait_triggered"
    await shoot(cid, play)
    selected = response(play, await play.store.read(cid))
    result = await CombatService(play).execute(cid, selected, principal_id="b")
    assert result.injury and result.injury.attack.effective_target == (11 if enabled else 13)
    assert result.injury.attack.total == 12 and result.injury.injury == (0 if enabled else 3)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "wait-seed"),
    )
    assert len(checks) == 4 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("original_first", [False, True])
async def test_throw_owner_damage_refuses_before_original_or_choice_entropy(
    tmp_path: Path, backend: str, original_first: bool
) -> None:
    cid, play = await ready(tmp_path, backend, "throw")
    await shoot(cid, play)
    selected = response(play, await play.store.read(cid))
    task = TaskService(play)
    await task.execute(
        cid,
        SetRealPlayClock(
            id="clock", actor_id="gm", expected_revision=selected.expected_revision, running=True
        ),
        principal_id="gm",
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    incoming = state.encounters[0].pending_defense
    assert incoming
    selected = response(play, before)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Thrown attacks do not support"):
        if original_first:
            await task.execute(
                cid,
                BeginOpponentAttack(
                    id="original",
                    actor_id="b",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    attack_id=incoming.id,
                    prepare_owner_damage=True,
                ),
                principal_id="gm",
            )
        else:
            await task.execute(
                cid,
                PrepareOwnerDamage(
                    id=selected.id,
                    actor_id="b",
                    expected_revision=state.revision,
                    response=selected,
                ),
                principal_id="b",
            )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("composed", [False, True])
async def test_actual_luck_rerolls_keep_the_original_posture_target(
    tmp_path: Path, backend: str, composed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = None
    if composed:
        cid, play, source = await luck_composed(tmp_path, backend)
    else:
        cid, play = await luck_inventory(tmp_path, backend, ranged=True)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    if source is not None:
        await declare_composed(play, cid, source)
    else:
        await shoot(cid, play)
    monkeypatch.setattr(generations, "ACTIVE", False)
    play.rng = RecordedDice([3, 3, 3])
    _, opened = await begin(play, cid)
    assert opened.check and opened.pending_id
    assert opened.check.effective_target == (10 if composed else 11)
    # Each first alternative misses only because the original target includes
    # B551. A fresh flag cannot turn those same dice into a native baseline hit.
    play.rng = RecordedDice(([3, 4, 4] if composed else [4, 4, 4]) + [3, 3, 3])
    _, selected = await choose(play, cid, opened.pending_id, principal="bob" if composed else "b")
    assert selected.luck and selected.check and selected.combat_json
    assert selected.check.effective_target == opened.check.effective_target
    assert selected.check.total == (11 if composed else 12)
    result = CombatResult.model_validate_json(selected.combat_json)
    assert result.injury and not result.injury.attack.outcome.succeeded
    assert result.injury.injury == 0 and result.injury.hp_after == 10
    assert play.rng.exhausted()
    final = play._load(await play.store.read(cid))
    assert snapshot(final).pending is None
    if not composed:
        assert final.resources.ammunition_loads[0].rounds == 5
