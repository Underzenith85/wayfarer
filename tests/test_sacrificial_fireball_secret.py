"""Private captured missile originals remain one actual roll under Task Luck."""

from pathlib import Path

import pytest
from test_sacrificial_fireball_fixture import pending_fireball

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.errors import AuthorizationError
from wayfarer.orchestration.combat import ChooseDefense
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("use_luck", [False, True])
async def test_secret_missile_reuses_private_release_original(
    tmp_path: Path, backend: str, use_luck: bool
) -> None:
    cid, play, _ = await pending_fireball(tmp_path, backend, luck=True)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack and attack.attack_roll
    play.rng = RecordedDice([])
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="secret-original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=attack.id,
            visibility="secret",
        ),
        principal_id="gm",
    )
    assert opened.check is None and opened.secret and play.rng.exhausted()
    visible = await task.pending(cid, principal_id="b")
    assert visible and visible.check is None and visible.combat_json is None
    with pytest.raises(AuthorizationError):
        await task.pending(cid, principal_id="c")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([4, 4, 4, 5, 5, 5] if use_luck else [6, 3, 3, 3])
    assert opened.pending_id is not None
    command = ChooseOpponentAttack(
        id="secret-choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=opened.pending_id,
        choice="use-luck" if use_luck else "accept",
        response=ChooseDefense(
            id="secret-choice",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    result = await task.execute(cid, command, principal_id="b" if use_luck else "gm")
    assert result.secret and play.rng.exhausted()
    assert result.check is None if use_luck else result.check == attack.attack_roll
    final = await play.store.read(cid)
    state = play._load(final)
    if use_luck:
        receipt = snapshot(state).luck.receipts[-1]
        assert receipt.attempts == ((3, 3, 3), (4, 4, 4), (5, 5, 5))
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    else:
        assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 4
    assert await task.execute(cid, command, principal_id="b" if use_luck else "gm") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_cached_original_seed_reexecution_keeps_three_attempts(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play, _ = await pending_fireball(tmp_path, backend, luck=True)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng, play.seeds = secrets, lambda: f"{5:064x}"
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="seed-secret",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=pending.id,
            visibility="secret",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    assert opened.pending_id is not None
    command = ChooseOpponentAttack(
        id="seed-secret-choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=opened.pending_id,
        choice="use-luck",
        response=ChooseDefense(
            id="seed-secret-choice",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    result = await task.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    receipt = snapshot(play._load(final)).luck.receipts[-1]
    assert receipt.attempts == ((3, 3, 3), (5, 4, 3), (4, 5, 6))
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "secret-seed-only"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
    assert await task.execute(cid, command, principal_id="b") == result
