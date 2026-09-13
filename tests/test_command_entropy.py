"""Command entropy durability, seed replay and privacy at real service boundaries."""

import asyncio
import json
import os
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign, seed_play
from test_actions import actor_setup, campaign, engine, resource_seed, world
from test_tactical import setup as hex_setup
from test_wave14 import Table

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice, draw_dice
from wayfarer.engine.rules.randomness import RNG_ALGORITHM, SeededRandom
from wayfarer.engine.simulation.actions import Inspect, Wait
from wayfarer.engine.simulation.events import action_result
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    CombatContext,
    CombatService,
    TakeCombatTurn,
    reduce_combat,
)
from wayfarer.orchestration.entropy import CommandRandom, commit_command, token_seed
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandEntropy
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.transport.common import ACCESS_KEY


def test_seeded_random_is_repeatable_for_dice_and_arbitrary_bounds() -> None:
    first, second = SeededRandom("00" * 32), SeededRandom("00" * 32)
    bounds = (1, 6, 6, 8, 127, 256, 257, 2**300 + 3) * 30
    values = [first.randbelow(bound) for bound in bounds]
    assert values == [second.randbelow(bound) for bound in bounds]
    assert all(0 <= value < bound for value, bound in zip(values, bounds, strict=True))
    assert values != [SeededRandom("01" * 32).randbelow(bound) for bound in bounds]
    with pytest.raises(ValidationError):
        first.randbelow(0)
    for bad in ("", "xx" * 32, "00" * 31, "00" * 33):
        with pytest.raises(ValidationError):
            SeededRandom(bad)


def test_seeded_random_v1_fixed_vector() -> None:
    rng = SeededRandom("00" * 32)
    assert [rng.randbelow(b) for b in [6] * 12 + [8, 256, 257]] == [
        3,
        3,
        1,
        1,
        1,
        2,
        0,
        0,
        5,
        0,
        2,
        4,
        4,
        125,
        61,
    ]


async def test_independent_campaigns_have_independent_streams(tmp_path: Path) -> None:
    play = build_play(tmp_path, engine(), seeds=token_seed, filename="independent.sqlite")
    store = play.store
    initial = campaign(engine())
    other = initial.copy()
    other["id"] = initial["id"] + "-other"
    await seed_campaign(store, initial)
    await seed_campaign(store, other)

    def reduce(state: Campaign) -> CommandReceipt:
        # Separate handles, as with a reducer and its checkpoint hook, share one stream.
        draws = draw_dice(CommandRandom()) + draw_dice(CommandRandom())
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome=json.dumps(draws))

    await asyncio.gather(
        *(
            commit_command(play, cid, "same-id", 0, "draws", reduce)
            for cid in (initial["id"], other["id"])
        )
    )
    first, second = [(await played(store, cid))[0] for cid in (initial["id"], other["id"])]
    assert first.entropy_seed != second.entropy_seed
    for record in (first, second):
        assert record.reexecutable and record.entropy_seed
        rng = SeededRandom(record.entropy_seed)
        assert json.loads(record.event["outcome"]) == list(draw_dice(rng) + draw_dice(rng))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_command_retry_race_restart_and_reexecution(
    tmp_path: Path, backend: str
) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url)
    else:
        store = AsyncSQLiteStore(tmp_path / "entropy.sqlite")
    reducer = engine()
    play = PlayService(store, reducer)
    initial = campaign(reducer)
    cid = initial["id"]
    await seed_play(play, initial, world(), resource_seed(), (actor_setup(),))
    before = play._load(await store.read(cid))
    command = Inspect(id="seeded-inspect", actor_id="a", expected_revision=0, target_id="chest")
    results = await asyncio.gather(
        *(play.execute(cid, command, authenticated_actor_id="a") for _ in range(6))
    )
    assert all(result == results[0] for result in results)
    history = await played(store, cid)
    assert len(history) == 1
    record = history[0]
    assert record.entropy_seed and len(record.entropy_seed) == 64
    assert record.rng_algorithm == RNG_ALGORITHM
    assert record.recorded_at_us is not None and record.recorded_at_us > 0
    assert record.reexecutable and record.actor_id == "a"
    replay = PlayService(store, reducer, rng=SeededRandom(record.entropy_seed))
    state, resolved_events = reducer.resolve(before, command, rng=replay.rng)
    result = action_result(resolved_events)
    state = replay.checkpoint(state, before=before)
    assert result == results[0] and result.check is not None
    assert state == play._load(record.state_after)
    restarted = PlayService(store, reducer)
    assert await restarted.execute(cid, command, authenticated_actor_id="a") == result
    assert await played(store, cid) == history
    with pytest.raises(ConflictError):
        await restarted.execute(
            cid, command.model_copy(update={"target_id": "hidden"}), authenticated_actor_id="a"
        )
    races = await asyncio.gather(
        *(
            restarted.execute(
                cid,
                Wait(id=f"wait-{i}", actor_id="a", expected_revision=1, ticks=1),
                authenticated_actor_id="a",
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ConflictError) for r in races) == 1
    assert len(await played(store, cid)) == 2
    assert record.entropy_seed not in json.dumps(record.state_after)
    assert record.entropy_seed not in json.dumps(record.event)
    assert record.entropy_seed not in repr(record)
    assert not replace(record, entropy_seed=None).reexecutable
    assert not replace(record, rng_algorithm="injected").reexecutable


async def test_reference_adventure_seed_replays_checkpoint_and_is_private(tmp_path: Path) -> None:
    table = Table(tmp_path / "reference.sqlite")
    await table.open()
    try:
        assert table.client
        table.client.app[ACCESS_KEY].play.rng = secrets
        await table.start()
        play = table.client.app[ACCESS_KEY].play
        play = play.for_campaign(await play.store.read(table.cid))
        before = play._load(await play.store.read(table.cid))
        command = Inspect(
            id="reference-seeded",
            actor_id="a",
            expected_revision=before.revision,
            target_id="manifest",
        )
        result = await play.execute(table.cid, command, authenticated_actor_id="a")
        assert result.check is not None
        record = (await played(play.store, table.cid))[-1]
        assert record.reexecutable and record.entropy_seed
        replay = PlayService(play.store, play.engine, rng=SeededRandom(record.entropy_seed))
        state, resolved_events = replay.engine.resolve(before, command, rng=replay.rng)
        replayed = action_result(resolved_events)
        state = replay.checkpoint(state, before=before)
        assert replayed == result and state == play._load(record.state_after)
        access = build_runtime(play)
        for principal in ("alice", "bob", "gm"):
            encoded = json.dumps(await access.read(table.cid, principal_id=principal))
            assert record.entropy_seed not in encoded
            assert "entropy_seed" not in encoded and "rng_algorithm" not in encoded
    finally:
        await table.close()


async def test_hex_combat_reexecutes_from_seed(tmp_path: Path) -> None:
    cid, play = await hex_setup(tmp_path)
    play.rng = secrets
    before = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="seeded-attack",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        mode_id="swing",
        target_id="b",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    record = (await played(play.store, cid))[-1]
    assert record.reexecutable and record.entropy_seed
    replay = PlayService(play.store, play.engine, rng=SeededRandom(record.entropy_seed))
    state, replayed = reduce_combat(before, command, CombatContext(replay, before))
    state = replay.checkpoint(state, before=before)
    assert replayed == result and state == play._load(record.state_after)
    access = build_runtime(play)
    for principal in ("alice", "bob", "gm", "spectator"):
        events = await access.events(cid, principal_id=principal, after=before.revision)
        encoded = json.dumps([event.model_dump(mode="json") for event in events])
        assert record.entropy_seed not in encoded
        assert "entropy_seed" not in encoded and "rng_algorithm" not in encoded


async def test_scope_resets_on_failure_and_explicit_test_rng_is_not_seed_replay(
    tmp_path: Path,
) -> None:
    play = build_play(tmp_path, engine(), filename="scope.sqlite")
    store = play.store
    initial = campaign(engine())
    await seed_campaign(store, initial)
    handle = CommandRandom()

    def fail(state: Campaign) -> CommandReceipt:
        draw_dice(handle)
        raise ValidationError("injected failure")

    with pytest.raises(ValidationError, match="injected failure"):
        await commit_command(play, initial["id"], "failed", 0, "failed", fail)
    assert await played(store, initial["id"]) == []
    with pytest.raises(ValidationError, match="command scope"):
        draw_dice(handle)

    def succeed(state: Campaign) -> CommandReceipt:
        assert draw_dice(handle) == (1, 2, 3)
        state["revision"] += 1
        return CommandReceipt(action="legacy", outcome="done")

    await commit_command(
        play, initial["id"], "succeeded", 0, "succeeded", succeed, rng=RecordedDice((1, 2, 3))
    )
    record = (await played(store, initial["id"]))[0]
    assert record.actor_id == "system" and not record.reexecutable
    assert record.rng_algorithm == "injected"
    assert CommandEntropy("00" * 32).seed not in repr(CommandEntropy("00" * 32))
