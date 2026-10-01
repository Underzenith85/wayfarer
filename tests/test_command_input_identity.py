"""Metadata preserves early duplicate identity without weakening receipts or snapshots."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path

import aiosqlite
import psycopg
import pytest
from support.runtime import open_store, seed_campaign
from test_resources import campaign, engine, seed

from wayfarer import validation
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.resources import Consume
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.resources import ResourceService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.command_inputs import KEY, ORIGINAL_INPUT, stamp
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.postgres import AsyncPostgresStore


def intent() -> dict[str, object]:
    return {
        "operation": "combat",
        "principal_id": "alice",
        "command": {
            "id": "same",
            "actor_id": "a",
            "expected_revision": 0,
            "target_id": "b",
            "amount": 1,
        },
    }


def advance(state: Campaign) -> CommandReceipt:
    state["revision"] += 1
    return CommandReceipt(action="resource", outcome="identity-fixture")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("encoding", ["legacy", "flat-generation", "retained-original"])
async def test_early_and_encoded_duplicate_preserve_identity(
    tmp_path: Path, backend: str, encoding: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    raw = json.dumps(intent(), sort_keys=True, separators=(",", ":"))
    encoded = (
        raw if encoding == "legacy" else stamp(raw, retain_original=encoding != "flat-generation")
    )
    await store.commit_turn(cid, "same", 0, encoded, advance, actor_id="a")
    before = await store.read(cid)
    history, stream = await store.history(cid), await store.stream(cid)
    assert await store.duplicate(cid, "same", raw) == before
    assert await store.duplicate(cid, "same", encoded) == before
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, "same", raw + " ")
    for change in (
        {"principal_id": "bob"},
        {"operation": "different"},
        {"actor_id": "b"},
        {"target_id": "c"},
        {"expected_revision": 1},
        {"amount": 2},
        {"amount": True},
        {"amount": 1.0},
    ):
        data = deepcopy(intent())
        if next(iter(change)) in {"principal_id", "operation"}:
            data.update(change)
        else:
            command = validation.mapping(data["command"])
            command.update(change)
            data["command"] = command
        changed = json.dumps(data, sort_keys=True, separators=(",", ":"))
        for requested in (changed, stamp(changed)):
            with pytest.raises(ConflictError, match="different input"):
                await store.duplicate(cid, "same", requested)
    assert await store.history(cid) == history
    assert await store.stream(cid) == stream
    assert await store.read(cid) == before == await store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("corruption", ["bytes", "future", "intent", "wrapper"])
async def test_duplicate_validates_stored_digest_and_explicit_metadata_even_on_exact_hash(
    tmp_path: Path, backend: str, corruption: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    raw = json.dumps(intent(), sort_keys=True, separators=(",", ":"))
    encoded = stamp(raw)
    await store.commit_turn(cid, "same", 0, encoded, advance, actor_id="a")
    broken = validation.mapping(validation.decode(encoded))
    expected = "digest"
    if corruption == "bytes":
        broken["principal_id"] = "bob"
    elif corruption == "future":
        broken[KEY] = 2
        expected = "Unsupported recorded Symptoms"
    elif corruption == "intent":
        broken[ORIGINAL_INPUT] = raw.replace('"alice"', '"bob"')
        expected = "Invalid recorded Symptoms original input"
    else:
        broken["symptom_command_input"] = "opaque"
        expected = "Invalid recorded Symptoms input wrapper"
    changed = json.dumps(broken, sort_keys=True, separators=(",", ":"))
    digest = payload_digest({"input": encoded if corruption == "bytes" else changed})
    db = await store._connect()
    try:
        bind = "?" if backend == "sqlite" else "%s"
        await db.execute(
            f"UPDATE command_log SET command_input={bind}, payload_hash={bind} WHERE campaign={bind} AND command_id={bind}",
            (changed, digest, cid, "same"),
        )
        await db.commit()
    finally:
        await db.close()
    for requested in (raw, encoded if corruption == "bytes" else changed):
        with pytest.raises(ValidationError, match=expected):
            await store.duplicate(cid, "same", requested)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_requested_future_metadata_cannot_match_known_intent(
    tmp_path: Path, backend: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    raw = json.dumps(intent())
    await store.commit_turn(cid, "same", 0, stamp(raw), advance, actor_id="a")
    for value in (None, 0, 2, True, "1"):
        requested = intent() | {KEY: value}
        with pytest.raises(ValidationError, match="Unsupported recorded Symptoms"):
            await store.duplicate(cid, "same", json.dumps(requested))


class SQLiteSnapshot(AsyncSQLiteStore):
    def __init__(self, path: Path, started: asyncio.Event, resume: asyncio.Event) -> None:
        super().__init__(path)
        self.started, self.resume = started, resume
        self.observed: int | None = None

    async def _duplicate(
        self, db: aiosqlite.Connection, cid: str, request_id: str, text: str
    ) -> Campaign | None:
        result = await super()._duplicate(db, cid, request_id, text)
        self.started.set()
        await self.resume.wait()
        cursor = await db.execute(
            "SELECT MAX(resulting_revision) FROM command_log WHERE campaign=?", (cid,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        assert row
        self.observed = validation.integer(row[0])
        return result


class PostgresSnapshot(AsyncPostgresStore):
    def __init__(self, url: str, started: asyncio.Event, resume: asyncio.Event) -> None:
        super().__init__(url)
        self.started, self.resume = started, resume
        self.observed: int | None = None

    async def _duplicate(
        self, db: psycopg.AsyncConnection[tuple[object, ...]], cid: str, command_id: str, text: str
    ) -> Campaign | None:
        result = await super()._duplicate(db, cid, command_id, text)
        self.started.set()
        await self.resume.wait()
        cursor = await db.execute(
            "SELECT MAX(resulting_revision) FROM command_log WHERE campaign=%s", (cid,)
        )
        row = await cursor.fetchone()
        assert row
        self.observed = validation.integer(row[0])
        return result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_duplicate_lookup_holds_its_snapshot_across_concurrent_commit(
    tmp_path: Path, backend: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    reducer = engine()
    service = ResourceService(store, reducer)
    initial = campaign(reducer)
    cid = initial["id"]
    await service.create(initial, seed())
    started, resume = asyncio.Event(), asyncio.Event()
    observer: SQLiteSnapshot | PostgresSnapshot
    if isinstance(store, AsyncSQLiteStore):
        async with aiosqlite.connect(store.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
        observer = SQLiteSnapshot(store.path, started, resume)
    else:
        observer = PostgresSnapshot(store.database_url, started, resume)
    value = Consume(id="same", actor_id="a", expected_revision=0, item_id="arrows", quantity=3)
    lookup = asyncio.create_task(observer.duplicate(cid, value.id, value.model_dump_json()))
    try:
        await asyncio.wait_for(started.wait(), timeout=10)
        await service.execute(cid, value, principal_id="a")
    finally:
        resume.set()
    assert await lookup is None
    assert observer.observed == 0  # The writer committed revision1 on another connection.
    assert await store.duplicate(cid, value.id, value.model_dump_json()) == await store.read(cid)
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, value.id, value.model_dump_json() + " ")
