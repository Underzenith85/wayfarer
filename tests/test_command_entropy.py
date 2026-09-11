"""Command entropy durability, seed replay and privacy at real service boundaries."""

import asyncio
import json
import os
import secrets
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from test_actions import actor_setup, campaign, engine, resource_seed, world
from test_tactical import setup as hex_setup
from test_wave14 import Table

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    CombatContext,
    CombatService,
    TakeCombatTurn,
    reduce_combat,
)
from wayfarer.orchestration.entropy import CommandRandom, commit_command
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandEntropy
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.checks import RecordedDice, draw_dice
from wayfarer.rules.randomness import RNG_ALGORITHM, SeededRandom
from wayfarer.simulation import ENGINE_VERSION
from wayfarer.simulation.actions import Inspect, Wait
from wayfarer.transport.campaign_api import ACCESS_KEY


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


async def test_legacy_command_metadata_migrates_without_inventing_a_seed(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    initial = campaign(engine())
    initial["revision"] = 1
    event = Event(input="legacy", action="ask", outcome="old", roll=None)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("INSERT INTO campaigns VALUES (?, ?)", (initial["id"], json.dumps(initial)))
        db.execute("""CREATE TABLE command_log (
            campaign TEXT, command_id TEXT, actor_id TEXT, expected_revision INTEGER,
            resulting_revision INTEGER, payload_hash TEXT, rules_version TEXT,
            schema_version INTEGER, event TEXT, state_after TEXT,
            PRIMARY KEY(campaign, command_id))""")
        db.execute(
            "INSERT INTO command_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                initial["id"],
                "old",
                "a",
                0,
                1,
                "old-hash",
                initial["rules"],
                1,
                json.dumps(event),
                json.dumps(initial),
            ),
        )
    store = AsyncSQLiteStore(path)
    histories = await asyncio.gather(*(store.history(initial["id"]) for _ in range(4)))
    assert all(history == histories[0] for history in histories)
    record = histories[0][0]
    assert record.entropy_seed is None and record.engine_version is None
    assert record.rng_algorithm is None and not record.reexecutable
    assert record.recorded_at_us is None
    assert record.state_after == initial


async def test_independent_campaigns_have_independent_streams(tmp_path: Path) -> None:
    store = AsyncSQLiteStore(tmp_path / "independent.sqlite")
    initial = campaign(engine())
    other = initial.copy()
    other["id"] = initial["id"] + "-other"
    await store.insert(initial)
    await store.insert(other)

    def reduce(state: Campaign) -> Event:
        # Separate handles, as with a reducer and its checkpoint hook, share one stream.
        draws = draw_dice(CommandRandom()) + draw_dice(CommandRandom())
        state["revision"] += 1
        return Event(input="draws", action="ask", outcome=json.dumps(draws), roll=None)

    await asyncio.gather(
        *(
            commit_command(store, cid, "same-id", 0, "draws", reduce)
            for cid in (initial["id"], other["id"])
        )
    )
    first, second = [(await store.history(cid))[0] for cid in (initial["id"], other["id"])]
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
    await play.create(initial, world(), resource_seed(), (actor_setup(),))
    before = play._load(await store.read(cid))
    command = Inspect(id="seeded-inspect", actor_id="a", expected_revision=0, target_id="chest")
    results = await asyncio.gather(
        *(play.execute(cid, command, authenticated_actor_id="a") for _ in range(6))
    )
    assert all(result == results[0] for result in results)
    history = await store.history(cid)
    assert len(history) == 1
    record = history[0]
    assert record.entropy_seed and len(record.entropy_seed) == 64
    assert record.engine_version == ENGINE_VERSION and record.rng_algorithm == RNG_ALGORITHM
    assert record.recorded_at_us is not None and record.recorded_at_us > 0
    assert record.reexecutable and record.actor_id == "a"
    replay = PlayService(store, reducer, rng=SeededRandom(record.entropy_seed))
    state, result = reducer.resolve(before, command, rng=replay.rng)
    state = replay.checkpoint(state, before=before)
    assert result == results[0] and result.check is not None
    assert state == play._load(record.state_after)
    restarted = PlayService(store, reducer)
    assert await restarted.execute(cid, command, authenticated_actor_id="a") == result
    assert await store.history(cid) == history
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
    assert len(await store.history(cid)) == 2
    assert record.entropy_seed not in json.dumps(record.state_after)
    assert record.entropy_seed not in json.dumps(record.event)
    assert record.entropy_seed not in repr(record)
    assert not replace(record, engine_version="different").reexecutable
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
        record = (await play.store.history(table.cid))[-1]
        assert record.reexecutable and record.entropy_seed
        replay = PlayService(play.store, play.engine, rng=SeededRandom(record.entropy_seed))
        state, replayed = replay.engine.resolve(before, command, rng=replay.rng)
        state = replay.checkpoint(state, before=before)
        assert replayed == result and state == play._load(record.state_after)
        access = CampaignAccess(play)
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
    record = (await play.store.history(cid))[-1]
    assert record.reexecutable and record.entropy_seed
    replay = PlayService(play.store, play.engine, rng=SeededRandom(record.entropy_seed))
    state, replayed = reduce_combat(before, command, CombatContext(replay, before))
    state = replay.checkpoint(state, before=before)
    assert replayed == result and state == play._load(record.state_after)
    access = CampaignAccess(play)
    for principal in ("alice", "bob", "gm", "spectator"):
        events = await access.events(cid, principal_id=principal, after=before.revision)
        encoded = json.dumps([event.model_dump(mode="json") for event in events])
        assert record.entropy_seed not in encoded
        assert "entropy_seed" not in encoded and "rng_algorithm" not in encoded


async def test_scope_resets_on_failure_and_explicit_test_rng_is_not_seed_replay(
    tmp_path: Path,
) -> None:
    store = AsyncSQLiteStore(tmp_path / "scope.sqlite")
    initial = campaign(engine())
    await store.insert(initial)
    handle = CommandRandom()

    def fail(state: Campaign) -> Event:
        draw_dice(handle)
        raise ValidationError("injected failure")

    with pytest.raises(ValidationError, match="injected failure"):
        await commit_command(store, initial["id"], "failed", 0, "failed", fail)
    assert await store.history(initial["id"]) == []
    with pytest.raises(ValidationError, match="command scope"):
        draw_dice(handle)

    def succeed(state: Campaign) -> Event:
        assert draw_dice(handle) == (1, 2, 3)
        state["revision"] += 1
        return Event(input="succeeded", action="ask", outcome="done", roll=None)

    await commit_command(
        store, initial["id"], "succeeded", 0, "succeeded", succeed, rng=RecordedDice((1, 2, 3))
    )
    record = (await store.history(initial["id"]))[0]
    assert record.actor_id == "system" and not record.reexecutable
    assert record.rng_algorithm == "injected"
    assert CommandEntropy("00" * 32).seed not in repr(CommandEntropy("00" * 32))
