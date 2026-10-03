"""Recorded direct and typed-task generations reexecute the actual missile injury."""

import secrets
from pathlib import Path

import pytest
from test_sacrificial_fireball_fixture import pending_fireball

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.persistence.replay import verify_commands


def seed() -> str:
    for n in range(10000):
        value = f"{n:064x}"
        rng = SeededRandom(value)
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if dodge == 6 and damage == 6:
            return value
    raise AssertionError("No source-derived Fireball interception seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_interposition_seed_reexecution_retains_captured_generation(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, command = await pending_fireball(tmp_path, backend)
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, seed
    result = await CombatService(play).execute(cid, command, principal_id="c")
    assert result.injury and result.injury.injury == 2
    final = await play.store.read(cid)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"missile-interposition"})
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == 1 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
    assert await CombatService(play).execute(cid, command, principal_id="c") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ordinary_absent_missile_generation_reexecutes_under_new_active_features(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"missile-interposition"})
    cid, play, response = await pending_fireball(tmp_path, backend, legacy_release=True)
    response = response.model_copy(
        update={"actor_id": "b", "sacrificial_for": None, "defense": "none"}
    )
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: "0" * 64
    await CombatService(play).execute(cid, response, principal_id="b")
    final = await play.store.read(cid)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"missile-interposition"})
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-seed-only"),
    )
    assert len(checks) == 1 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_task_interposition_seed_reexecution_preserves_generation_and_owner(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration import task_combat_generations
    from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
    from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, PrepareOwnerDamage
    from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
    from wayfarer.orchestration.tasks import TaskService

    cid, play, response = await pending_fireball(tmp_path, backend, luck=True)
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
    response = response.model_copy(update={"expected_revision": state.revision})
    command = PrepareOwnerDamage(
        id=response.id,
        actor_id=response.actor_id,
        expected_revision=response.expected_revision,
        response=response,
    )
    play.rng, play.seeds = secrets, seed
    await task.execute(cid, command, principal_id="c")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending) and pending.actor_id == "a"
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
    final = await play.store.read(cid)
    monkeypatch.setattr(
        task_combat_generations,
        "ACTIVE",
        task_combat_generations.ACTIVE - {"missile-interposition"},
    )
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "task-seed-only"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
