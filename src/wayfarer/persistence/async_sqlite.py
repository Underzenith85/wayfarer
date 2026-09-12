"""Async SQLite adapter with atomic command handling."""

import asyncio
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import aiosqlite

from wayfarer import validation
from wayfarer.engine.simulation.events import (
    EVENT_ADAPTER,
    EngineEvent,
    StatePatched,
    command_events,
    digest,
    document,
    fold,
)
from wayfarer.engine.simulation.scenario_document import ScenarioBoundary
from wayfarer.errors import ConflictError, NotFoundError, StorageError, ValidationError
from wayfarer.models import Campaign, CommandReceipt, TurnResult
from wayfarer.persistence import snapshots
from wayfarer.persistence.events import (
    COMMAND_SCHEMA_VERSION,
    CommandEntropy,
    CommandOrigin,
    CommandRecord,
    CommandResolution,
    StoredEvent,
    command_scenario,
    payload_digest,
    upcast_command,
)
from wayfarer.persistence.upcasters import EVENT_UPCASTERS, read_event

SNAPSHOT_INTERVAL = 10


class AsyncSQLiteStore:
    def __init__(
        self, path: Path, timeout: float = 10.0, *, snapshot_interval: int = SNAPSHOT_INTERVAL
    ) -> None:
        self.path = path
        self.timeout = timeout
        if snapshot_interval < 0:
            raise ValueError("Snapshot interval must be nonnegative")
        self.snapshot_interval = snapshot_interval

    async def _connect(self) -> aiosqlite.Connection:
        await asyncio.to_thread(self.path.parent.mkdir, parents=True, exist_ok=True)
        db = await aiosqlite.connect(self.path, timeout=self.timeout)
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))"
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS command_log (
                campaign TEXT NOT NULL,
                command_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                expected_revision INTEGER NOT NULL,
                resulting_revision INTEGER NOT NULL,
                payload_hash TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                event TEXT NOT NULL,
                state_after TEXT NOT NULL,
                PRIMARY KEY(campaign, command_id)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                campaign TEXT NOT NULL,
                revision INTEGER NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY(campaign, revision)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS stream_genesis (
                campaign TEXT PRIMARY KEY, revision BIGINT NOT NULL, state TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS event_stream (
                campaign TEXT NOT NULL, revision BIGINT NOT NULL, ordinal INTEGER NOT NULL,
                command_id TEXT NOT NULL, schema_version INTEGER NOT NULL, event TEXT NOT NULL,
                PRIMARY KEY(campaign, revision, ordinal)
            )"""
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS narration_stream (campaign TEXT NOT NULL, revision BIGINT NOT NULL, message_index INTEGER NOT NULL, text TEXT NOT NULL, PRIMARY KEY(campaign, message_index))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS checkpoint_digests (campaign TEXT NOT NULL, revision BIGINT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(campaign, revision))"
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS narration_migrations (campaign TEXT PRIMARY KEY)"
        )
        cursor = await db.execute("PRAGMA table_info(command_log)")
        columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        metadata = (
            "entropy_seed",
            "engine_version",
            "rng_algorithm",
            "recorded_at_us",
            "origin_json",
            "command_input",
            "scenario_boundary_json",
        )
        if not set(metadata) <= columns:
            # Recheck under the writer lock; normal reads need no migration lock.
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute("PRAGMA table_info(command_log)")
            columns = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            for column in metadata:
                if column not in columns:
                    kind = "INTEGER" if column == "recorded_at_us" else "TEXT"
                    await db.execute(f"ALTER TABLE command_log ADD COLUMN {column} {kind}")
        await db.commit()
        return db

    @staticmethod
    async def _cached(db: aiosqlite.Connection, cid: str) -> Campaign:
        cursor = await db.execute("SELECT state FROM campaigns WHERE id=?", (cid,))
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            raise NotFoundError("Campaign not found")
        return validation.campaign(validation.decode(row[0]))

    async def insert(self, state: Campaign) -> None:
        db = await self._connect()
        try:
            await db.execute(
                "INSERT INTO campaigns VALUES (?, ?)", (state["id"], json.dumps(state))
            )
            if self.snapshot_interval:
                await db.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?)",
                    (state["id"], state["revision"], snapshots.encode(state)),
                )
            await db.execute(
                "INSERT INTO checkpoint_digests (campaign, revision, digest) VALUES (?, ?, ?)",
                (state["id"], state["revision"], digest(document(state))),
            )
            await db.execute(
                "INSERT INTO stream_genesis (campaign, revision, state) VALUES (?, ?, ?)",
                (state["id"], state["revision"], json.dumps(state)),
            )
            await db.commit()
        except aiosqlite.Error as exc:
            await db.rollback()
            raise StorageError("Unable to save campaign") from exc
        finally:
            await db.close()

    async def _ensure_digests(self, db: aiosqlite.Connection, cid: str, initial: Campaign) -> None:
        cursor = await db.execute(
            "SELECT 1 FROM checkpoint_digests WHERE campaign=? LIMIT 1", (cid,)
        )
        if await cursor.fetchone() is not None:
            return
        await db.execute(
            "INSERT INTO checkpoint_digests (campaign, revision, digest) VALUES (?, ?, ?) ON CONFLICT(campaign, revision) DO NOTHING",
            (cid, initial["revision"], digest(document(initial))),
        )
        cursor = await db.execute(
            "SELECT revision, schema_version, event FROM event_stream WHERE campaign=? ORDER BY revision, ordinal",
            (cid,),
        )
        for row in await cursor.fetchall():
            event = read_event(validation.string(row[2]), validation.integer(row[1]))
            if isinstance(event, StatePatched) and event.scope == "campaign":
                await db.execute(
                    "INSERT INTO checkpoint_digests (campaign, revision, digest) VALUES (?, ?, ?) ON CONFLICT(campaign, revision) DO UPDATE SET digest=excluded.digest",
                    (cid, row[0], event.after_digest),
                )

    async def _read(
        self, db: aiosqlite.Connection, cid: str, *, lock: bool = False, through: int | None = None
    ) -> Campaign:
        cursor = await db.execute("SELECT id FROM campaigns WHERE id=?", (cid,))
        if await cursor.fetchone() is None:
            raise NotFoundError("Campaign not found")
        await self._ensure_stream(db, cid)
        cursor = await db.execute("SELECT state FROM stream_genesis WHERE campaign=?", (cid,))
        initial = await cursor.fetchone()
        assert initial is not None
        genesis = snapshots.decode(initial[0])
        await self._ensure_digests(db, cid, genesis)
        cursor = await db.execute("SELECT revision, state FROM snapshots WHERE campaign=?", (cid,))
        caches = [(validation.integer(row[0]), row[1]) for row in await cursor.fetchall()]
        cursor = await db.execute(
            "SELECT revision, digest FROM checkpoint_digests WHERE campaign=?", (cid,)
        )
        digests = {
            validation.integer(row[0]): validation.string(row[1]) for row in await cursor.fetchall()
        }
        maximum = through if through is not None else 2**63 - 1
        checkpoint, cutoff = snapshots.select_checkpoint(genesis, caches, digests, maximum)
        cursor = await db.execute(
            "SELECT command_id, revision, ordinal, schema_version, event FROM event_stream WHERE campaign=? AND revision>? AND revision<=? ORDER BY revision, ordinal",
            (cid, cutoff if cutoff is not None else -1, maximum),
        )
        events: list[StoredEvent] = []
        for row in await cursor.fetchall():
            event = read_event(validation.string(row[4]), validation.integer(row[3]))
            events.append(
                StoredEvent(
                    cid,
                    validation.string(row[0]),
                    validation.integer(row[1]),
                    validation.integer(row[2]),
                    event,
                    EVENT_UPCASTERS.current[event.kind],
                )
            )
        return snapshots.materialize(checkpoint, events, [], through=through)

    async def _import_narration(self, db: aiosqlite.Connection, cid: str) -> None:
        cursor = await db.execute("SELECT 1 FROM narration_migrations WHERE campaign=?", (cid,))
        if await cursor.fetchone() is not None:
            return
        cursor = await db.execute("SELECT state FROM campaigns WHERE id=?", (cid,))
        row = await cursor.fetchone()
        if row is not None:
            try:
                cached = snapshots.decode(row[0])
            except ValueError, KeyError, TypeError, ValidationError:
                cached = None
            if cached is not None:
                for index, message in enumerate(cached["messages"]):
                    if "flavor" in message:
                        await db.execute(
                            "INSERT INTO narration_stream (campaign, revision, message_index, text) VALUES (?, ?, ?, ?) ON CONFLICT(campaign, message_index) DO NOTHING",
                            (cid, 0, index, message["flavor"]),
                        )
        await db.execute(
            "INSERT INTO narration_migrations (campaign) VALUES (?) ON CONFLICT(campaign) DO NOTHING",
            (cid,),
        )

    async def read(self, cid: str) -> Campaign:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._read(db, cid, lock=True)
            await self._import_narration(db, cid)
            cursor = await db.execute(
                "SELECT message_index, text FROM narration_stream WHERE campaign=? AND revision<=? ORDER BY revision",
                (cid, state["revision"]),
            )
            for row in await cursor.fetchall():
                index = validation.integer(row[0])
                if 0 <= index < len(state["messages"]):
                    state["messages"][index]["flavor"] = validation.string(row[1])
            await db.commit()
            return state
        finally:
            await db.close()

    async def listing(self) -> list[dict[str, str]]:
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT id FROM campaigns ORDER BY rowid DESC")
            rows = await cursor.fetchall()
            await cursor.close()
            states = [await self._read(db, validation.string(row[0])) for row in rows]
            return [
                {"id": s["id"], "title": s["scenario"]["title"], "name": s["character"]["name"]}
                for s in states
            ]
        finally:
            await db.close()

    async def _duplicate(
        self, db: aiosqlite.Connection, cid: str, request_id: str, text: str
    ) -> Campaign | None:
        digest = payload_digest({"input": text})
        cursor = await db.execute(
            "SELECT payload_hash, resulting_revision FROM command_log WHERE campaign=? AND command_id=?",
            (cid, request_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is not None:
            if row[0] != digest:
                raise ConflictError("Request ID already used for different input")
            return await self._read(db, cid, through=validation.integer(row[1]))
        cursor = await db.execute(
            "SELECT payload FROM events WHERE campaign=? AND request_id=?", (cid, request_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return None
        if validation.mapping(validation.decode(row[0])).get("input") != text:
            raise ConflictError("Request ID already used for different input")
        return await self._read(db, cid)

    async def duplicate(self, cid: str, request_id: str, text: str) -> Campaign | None:
        db = await self._connect()
        try:
            return await self._duplicate(db, cid, request_id, text)
        finally:
            await db.close()

    async def commit_turn(
        self,
        cid: str,
        request_id: str,
        revision: int,
        text: str,
        resolve: Callable[[Campaign], CommandReceipt | CommandResolution],
        *,
        actor_id: str = "system",
        entropy: CommandEntropy | None = None,
        recorded_at_us: int | None = None,
        origin: CommandOrigin | None = None,
    ) -> TurnResult:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._read(db, cid)
            await self._import_narration(db, cid)
            duplicate = await self._duplicate(db, cid, request_id, text)
            if duplicate is not None:
                await db.rollback()
                return {"kind": "replayed", "state": duplicate}
            if state["revision"] != revision:
                raise ConflictError("Campaign changed. Refresh before retrying.")
            await self._ensure_stream(db, cid)
            before = deepcopy(state)
            resolved = resolve(state)
            event = resolved.receipt if isinstance(resolved, CommandResolution) else resolved
            validation.fields(validation.mapping(event), {"action", "outcome"})
            emitted = (
                resolved.events
                if isinstance(resolved, CommandResolution)
                else command_events(before, state, event, actor_id)
            )
            if not emitted or document(fold(before, emitted)) != document(state):
                raise StorageError("Command events do not reproduce the committed state")
            await self._append_events(db, cid, request_id, state["revision"], emitted)
            await db.execute(
                "INSERT INTO checkpoint_digests (campaign, revision, digest) VALUES (?, ?, ?) ON CONFLICT(campaign, revision) DO UPDATE SET digest=excluded.digest",
                (cid, state["revision"], digest(document(state))),
            )
            await db.execute(
                "INSERT INTO events VALUES (?,?,?)", (cid, request_id, json.dumps(event))
            )
            input_digest = payload_digest({"input": text})
            await db.execute(
                """INSERT INTO command_log (
                    campaign, command_id, actor_id, expected_revision,
                    resulting_revision, payload_hash, rules_version,
                    schema_version, event, state_after, entropy_seed, engine_version, rng_algorithm, recorded_at_us, origin_json, command_input, scenario_boundary_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cid,
                    request_id,
                    actor_id,
                    revision,
                    state["revision"],
                    input_digest,
                    state["rules"],
                    COMMAND_SCHEMA_VERSION,
                    json.dumps(event),
                    "{}",
                    entropy.seed if entropy else None,
                    None,  # Retired engine_version column; retained for existing databases.
                    entropy.rng_algorithm if entropy else None,
                    recorded_at_us,
                    origin.model_dump_json() if origin else None,
                    text,
                    command_scenario(state),
                ),
            )
            if self.snapshot_interval and state["revision"] % self.snapshot_interval == 0:
                await db.execute(
                    "UPDATE campaigns SET state=? WHERE id=?", (snapshots.encode(state), cid)
                )
                await db.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?)",
                    (cid, state["revision"], snapshots.encode(state)),
                )
            await db.commit()
            return {"kind": "committed", "state": state, "event": event}
        except ConflictError, NotFoundError:
            await db.rollback()
            raise
        except aiosqlite.Error as exc:
            await db.rollback()
            raise StorageError("Unable to commit turn") from exc
        finally:
            await db.close()

    async def history(self, cid: str) -> list[CommandRecord]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                """SELECT command_id, actor_id, expected_revision, resulting_revision,
                          payload_hash, rules_version, schema_version, event, state_after, entropy_seed, engine_version, rng_algorithm, recorded_at_us, origin_json, command_input, scenario_boundary_json
                   FROM command_log WHERE campaign=? ORDER BY resulting_revision""",
                (cid,),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            states = {state["revision"]: state for state, _ in await self.stream_states(cid)}
            return [
                upcast_command(
                    CommandRecord(
                        campaign_id=cid,
                        command_id=row[0],
                        actor_id=row[1],
                        expected_revision=row[2],
                        resulting_revision=row[3],
                        payload_hash=row[4],
                        rules_version=row[5],
                        schema_version=row[6],
                        event=self._event(row[7]),
                        state_after=states[validation.integer(row[3])],
                        entropy_seed=row[9],
                        rng_algorithm=row[11],
                        recorded_at_us=row[12],
                        command_input=row[14]
                        if row[14] is not None
                        else self._legacy_input(row[7]),
                        scenario_boundary=ScenarioBoundary.model_validate_json(row[15])
                        if row[15]
                        else None,
                        origin=CommandOrigin.model_validate_json(row[13])
                        if row[13] is not None
                        else None,
                    )
                )
                for row in rows
            ]
        finally:
            await db.close()

    @staticmethod
    def _legacy_input(raw: str) -> str | None:
        data = validation.mapping(validation.decode(raw))
        value = data.get("input")
        return validation.string(value) if value is not None else None

    @staticmethod
    def _event(raw: str) -> CommandReceipt:
        data = validation.mapping(validation.decode(raw))
        return CommandReceipt(
            action=validation.event_action(data["action"]),
            outcome=validation.string(data["outcome"]),
        )

    async def replay(self, cid: str, revision: int | None = None) -> Campaign:
        db = await self._connect()
        try:
            state = await self._read(db, cid, through=revision)
            await db.commit()
            return state
        finally:
            await db.close()

    async def save_narration(self, cid: str, revision: int, narration: str) -> None:
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            state = await self._read(db, cid, lock=True)
            if state["revision"] == revision and state["messages"]:
                await db.execute(
                    "INSERT INTO narration_stream (campaign, revision, message_index, text) VALUES (?, ?, ?, ?) ON CONFLICT(campaign, message_index) DO NOTHING",
                    (cid, revision, len(state["messages"]) - 1, narration),
                )
            await db.commit()
        finally:
            await db.close()

    @staticmethod
    async def _append_events(
        db: aiosqlite.Connection,
        cid: str,
        command_id: str,
        revision: int,
        events: list[EngineEvent],
    ) -> None:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(ordinal) + 1, 0) FROM event_stream WHERE campaign=? AND revision=?",
            (cid, revision),
        )
        row = await cursor.fetchone()
        assert row is not None
        offset = int(str(row[0]))
        for ordinal, event in enumerate(events, start=offset):
            await db.execute(
                "INSERT INTO event_stream (campaign, revision, ordinal, command_id, schema_version, event) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    revision,
                    ordinal,
                    command_id,
                    EVENT_UPCASTERS.current[event.kind],
                    EVENT_ADAPTER.dump_json(event).decode(),
                ),
            )

    async def _ensure_stream(self, db: aiosqlite.Connection, cid: str) -> None:
        cursor = await db.execute("SELECT campaign FROM stream_genesis WHERE campaign=?", (cid,))
        if await cursor.fetchone() is not None:
            return
        cursor = await db.execute(
            "SELECT revision, state FROM snapshots WHERE campaign=? ORDER BY revision LIMIT 1",
            (cid,),
        )
        initial = await cursor.fetchone()
        if initial is None:
            # Pre-event-store databases may have only their current campaign row.
            # Preserve that explicit boundary; earlier state cannot be invented.
            before = await self._cached(db, cid)
            revision = before["revision"]
        else:
            revision = int(str(initial[0]))
            before = snapshots.decode(initial[1])
        await db.execute(
            "INSERT INTO stream_genesis (campaign, revision, state) VALUES (?, ?, ?)",
            (cid, revision, json.dumps(before)),
        )
        cursor = await db.execute(
            "SELECT command_id, actor_id, resulting_revision, event, state_after FROM command_log WHERE campaign=? AND resulting_revision>? ORDER BY resulting_revision",
            (cid, revision),
        )
        for row in await cursor.fetchall():
            after = validation.campaign(validation.decode(row[4]))
            transcript = self._event(row[3])
            events = command_events(before, after, transcript, str(row[1]))
            await self._append_events(db, cid, str(row[0]), int(str(row[2])), events)
            before = after

    async def stream_states(
        self, cid: str, *, through: int | None = None
    ) -> list[tuple[Campaign, tuple[StoredEvent, ...]]]:
        """Rebuild each retained revision from genesis and the dedicated stream."""
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await self._ensure_stream(db, cid)
            cursor = await db.execute("SELECT state FROM stream_genesis WHERE campaign=?", (cid,))
            initial = await cursor.fetchone()
            assert initial is not None
            state = validation.campaign(validation.decode(initial[0]))
            cursor = await db.execute(
                "SELECT command_id, revision, ordinal, schema_version, event FROM event_stream WHERE campaign=? AND revision<=? ORDER BY revision, ordinal",
                (cid, through if through is not None else 2**63 - 1),
            )
            rows = await cursor.fetchall()
            await db.commit()
            result: list[tuple[Campaign, tuple[StoredEvent, ...]]] = [(state, ())]
            grouped: list[StoredEvent] = []
            for row in rows:
                decoded = read_event(validation.string(row[4]), int(str(row[3])))
                event = StoredEvent(
                    cid,
                    str(row[0]),
                    int(str(row[1])),
                    int(str(row[2])),
                    decoded,
                    EVENT_UPCASTERS.current[decoded.kind],
                )
                if grouped and event.revision != grouped[0].revision:
                    state = fold(state, [e.event for e in grouped])
                    result.append((state, tuple(grouped)))
                    grouped = []
                grouped.append(event)
            if grouped:
                state = fold(state, [e.event for e in grouped])
                result.append((state, tuple(grouped)))
            return result
        finally:
            await db.close()

    async def schema_usage(self) -> list[dict[str, object]]:
        """Raw retained versions and per-campaign snapshot coverage for retirement audits."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT e.campaign, e.schema_version, e.event, e.revision, "
                "(SELECT MAX(s.revision) FROM snapshots s WHERE s.campaign=e.campaign) "
                "FROM event_stream e ORDER BY e.campaign, e.revision, e.ordinal"
            )
            rows = await cursor.fetchall()
            usage: dict[tuple[str, str, int], dict[str, object]] = {}
            for row in rows:
                kind = validation.string(
                    validation.mapping(validation.decode(validation.string(row[2])))["kind"]
                )
                key = (validation.string(row[0]), kind, validation.integer(row[1]))
                usage[key] = {
                    "campaign": key[0],
                    "kind": kind,
                    "version": key[2],
                    "last_revision": validation.integer(row[3]),
                    "snapshot_revision": row[4],
                }
            coverage: dict[str, int | None] = {}
            for cid, _, _ in usage:
                if cid in coverage:
                    continue
                # Validate coverage before declaring an old schema retireable.
                await self._read(db, cid)
                cursor = await db.execute(
                    "SELECT state FROM stream_genesis WHERE campaign=?", (cid,)
                )
                initial = await cursor.fetchone()
                assert initial is not None
                cursor = await db.execute(
                    "SELECT revision, state FROM snapshots WHERE campaign=?", (cid,)
                )
                caches = [(validation.integer(row[0]), row[1]) for row in await cursor.fetchall()]
                cursor = await db.execute(
                    "SELECT revision, digest FROM checkpoint_digests WHERE campaign=?", (cid,)
                )
                digests = {
                    validation.integer(row[0]): validation.string(row[1])
                    for row in await cursor.fetchall()
                }
                _, coverage[cid] = snapshots.select_checkpoint(
                    snapshots.decode(initial[0]), caches, digests, 2**63 - 1
                )
            for key, item in usage.items():
                item["snapshot_revision"] = coverage[key[0]]
            await db.commit()
            return list(usage.values())
        finally:
            await db.close()

    async def stream(self, cid: str, *, after: int = 0) -> list[StoredEvent]:
        return [
            event
            for _, events in await self.stream_states(cid)
            for event in events
            if event.revision > after
        ]
