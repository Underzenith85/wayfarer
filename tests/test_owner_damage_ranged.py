"""B66/B373: per-hit damage selection changes real injury without re-firing a burst."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_gurps_maneuvers import turn
from test_opponent_attack_inventory import fixture
from test_owner_damage_host import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.ranged.damage_records import PreparedRangedDamage
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_burst_choices_commit_each_selected_injury_then_open_only_next_damage(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=True)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    play.rng = RecordedDice((3, 3, 4) + (() if secret else (1,)))
    prepared_command, _ = await begin(play, cid, secret=secret, principal="gm" if secret else "b")
    first = play._load(await play.store.read(cid))
    first_pending = snapshot(first).pending
    assert isinstance(first_pending, InventoryDamagePending) and isinstance(
        first_pending.preparation, PreparedRangedDamage
    )
    assert (
        first_pending.preparation.context.impacts == 3
        and first_pending.preparation.progress.index == 0
    )
    assert next(p.current for p in first.resources.pools if p.id == "hp:b") == 10
    assert not first.resources.ammunition_loads and play.rng.exhausted()
    commands = []
    outcomes = []
    for index, hp_after in enumerate((4, 2, 1)):
        state = play._load(await play.store.read(cid))
        pending = snapshot(state).pending
        assert isinstance(pending, InventoryDamagePending) and isinstance(
            pending.preparation, PreparedRangedDamage
        )
        assert pending.preparation.progress.index == index
        command = ChooseOwnerDamage(
            id="damage-" + str(index),
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck" if index == 0 else "accept",
        )
        if index == 0:
            # Original1, alternatives2/6; only selected6 needs its major-wound HT.
            play.rng = RecordedDice(
                ((1,) if secret else ()) + (2, 6, 2, 2, 2) + (() if secret else (2,))
            )
        elif index == 1:
            play.rng = RecordedDice((2,) if secret else (1,))
        else:
            play.rng = RecordedDice((1,) if secret else ())
        principal = "a" if index == 0 or not secret else "gm"
        outcome = await TaskService(play).execute(cid, command, principal_id=principal)
        commands.append((command, principal))
        outcomes.append(outcome)
        after = play._load(await play.store.read(cid))
        assert next(p.current for p in after.resources.pools if p.id == "hp:b") == hp_after
        assert not after.resources.ammunition_loads and len(snapshot(after).luck.receipts) == 1
        assert play.rng.exhausted()
        if index < 2:
            assert outcome.status == "pending" and after.encounters[0].pending_defense is not None
            assert not after.encounters[0].wounds and after.encounters[0].current_actor_id == "a"
            next_pending = snapshot(after).pending
            assert next_pending is not None and next_pending.id != pending.id
            play.rng = RecordedDice(())
            with pytest.raises(ValidationError, match="cooling down"):
                await TaskService(play).execute(
                    cid,
                    ChooseOwnerDamage(
                        id="unavailable-" + str(index),
                        actor_id="a",
                        expected_revision=after.revision,
                        pending_id=next_pending.id,
                        choice="use-luck",
                    ),
                    principal_id="a",
                )
        else:
            assert outcome.status == "completed" and snapshot(after).pending is None
            assert (
                after.encounters[0].current_actor_id == "b" and len(after.encounters[0].wounds) == 1
            )
    wound = after.encounters[0].wounds[0]
    assert wound.attack.dice == (3, 3, 4) and wound.damage_dice == (6, 2, 1)
    assert wound.per_hit_injury == (6, 2, 1) and wound.injury == 9 and wound.hp_after == 1
    saved = await play.store.read(cid)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    for (command, principal), outcome in zip(commands, outcomes, strict=True):
        assert await TaskService(restarted).execute(cid, command, principal_id=principal) == outcome
    await TaskService(restarted).execute(
        cid, prepared_command, principal_id="gm" if secret else "b"
    )
    assert await restarted.store.read(cid) == saved == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ranged_candidate_failure_rolls_back_selected_injury_and_next_original(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=True)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    play.rng = RecordedDice((3, 3, 4, 1))
    await begin(play, cid, principal="b")
    before = await play.store.read(cid)
    state = play._load(before)
    pending = snapshot(state).pending
    assert pending
    command = ChooseOwnerDamage(
        id="choose",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    history, events = await play.store.history(cid), await play.store.stream(cid)
    faces = (2, 6, 2, 2, 2, 1)
    failed = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice(faces), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failed).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    play.rng = RecordedDice(faces)
    result = await TaskService(play).execute(cid, command, principal_id="a")
    assert result.status == "pending" and result.luck
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 4
    assert not after.resources.ammunition_loads and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("same", [False, True])
async def test_ranged_independent_choices_cannot_repeat_the_first_injury(
    tmp_path: Path, backend: str, same: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=True)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    play.rng = RecordedDice((3, 3, 4, 1))
    await begin(play, cid, principal="b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert pending
    peers = [
        build_play(
            tmp_path,
            play.engine,
            backend=backend,
            instants=play.instants,
            rng=RecordedDice((2, 6, 2, 2, 2, 1)),
        )
        for _ in range(2)
    ]
    commands = [
        ChooseOwnerDamage(
            id=identifier,
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        )
        for identifier in ("one", "one" if same else "two")
    ]
    outcomes = await asyncio.gather(
        *(
            TaskService(peer).execute(cid, command, principal_id="a")
            for peer, command in zip(peers, commands, strict=True)
        ),
        return_exceptions=True,
    )
    if same:
        assert outcomes[0] == outcomes[1]
    else:
        assert sum(isinstance(outcome, ConflictError) for outcome in outcomes) == 1
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 4
    assert len(snapshot(after).luck.receipts) == 1 and not after.resources.ammunition_loads
    following = snapshot(after).pending
    assert isinstance(following, InventoryDamagePending) and isinstance(
        following.preparation, PreparedRangedDamage
    )
    assert following.preparation.progress.index == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_every_burst_phase_reexecutes_from_only_its_seed_and_folds_exactly(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, ranged=True)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "01" * 32
    await begin(play, cid, principal="b")
    index = 0
    while (pending := snapshot(play._load(await play.store.read(cid))).pending) is not None:
        assert isinstance(pending, InventoryDamagePending) and isinstance(
            pending.preparation, PreparedRangedDamage
        )
        state = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="seed-hit-" + str(index),
                actor_id="a",
                expected_revision=state.revision,
                pending_id=pending.id,
                choice="use-luck" if index == 0 else "accept",
            ),
            principal_id="a",
        )
        index += 1
    assert index > 1
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == index + 1 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
