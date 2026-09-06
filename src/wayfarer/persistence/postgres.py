"""PostgreSQL event store with serialized campaign command transactions."""

import json
from collections.abc import Callable

import psycopg

from wayfarer import validation
from wayfarer.errors import ConflictError, NotFoundError, StorageError
from wayfarer.models import Campaign, Event, TurnResult
from wayfarer.persistence.events import EVENT_SCHEMA_VERSION, StoredEvent, payload_digest

SNAPSHOT_INTERVAL = 10


class AsyncPostgresStore:
    def __init__(self, database_url: str, timeout: float = 10.0) -> None:
        self.database_url = database_url
        self.timeout = timeout

    async def _connect(self) -> psycopg.AsyncConnection[tuple[object, ...]]:
        try:
            db = await psycopg.AsyncConnection.connect(
                self.database_url, connect_timeout=max(1, round(self.timeout))
            )
            await self._schema(db)
            return db
        except psycopg.Error as exc:
            raise StorageError("Unable to connect to PostgreSQL") from exc

    @staticmethod
    async def _schema(db: psycopg.AsyncConnection[tuple[object, ...]]) -> None:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, state JSONB NOT NULL)"
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS command_log (
                campaign TEXT NOT NULL REFERENCES campaigns(id),
                command_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                expected_revision BIGINT NOT NULL,
                resulting_revision BIGINT NOT NULL,
                payload_hash TEXT NOT NULL,
                rules_version TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                event JSONB NOT NULL,
                state_after JSONB NOT NULL,
                PRIMARY KEY(campaign, command_id),
                UNIQUE(campaign, resulting_revision)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                campaign TEXT NOT NULL REFERENCES campaigns(id),
                revision BIGINT NOT NULL,
                state JSONB NOT NULL,
                PRIMARY KEY(campaign, revision)
            )"""
        )
        await db.commit()

    @staticmethod
    def _campaign(value: object) -> Campaign:
        if isinstance(value, str | bytes):
            return validation.campaign(validation.decode(value))
        return validation.campaign(value)

    @staticmethod
    async def _read(
        db: psycopg.AsyncConnection[tuple[object, ...]], cid: str, *, lock: bool = False
    ) -> Campaign:
        suffix = " FOR UPDATE" if lock else ""
        cursor = await db.execute("SELECT state FROM campaigns WHERE id=%s" + suffix, (cid,))
        row = await cursor.fetchone()
        if row is None:
            raise NotFoundError("Campaign not found")
        return AsyncPostgresStore._campaign(row[0])

    async def insert(self, state: Campaign) -> None:
        db = await self._connect()
        try:
            async with db.transaction():
                await db.execute(
                    "INSERT INTO campaigns (id, state) VALUES (%s, %s::jsonb)",
                    (state["id"], json.dumps(state)),
                )
                await db.execute(
                    "INSERT INTO snapshots (campaign, revision, state) VALUES (%s, %s, %s::jsonb)",
                    (state["id"], state["revision"], json.dumps(state)),
                )
        except psycopg.Error as exc:
            raise StorageError("Unable to save campaign") from exc
        finally:
            await db.close()

    async def read(self, cid: str) -> Campaign:
        db = await self._connect()
        try:
            return await self._read(db, cid)
        finally:
            await db.close()

    async def listing(self) -> list[dict[str, str]]:
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT state FROM campaigns ORDER BY id DESC")
            rows = await cursor.fetchall()
            states = [self._campaign(row[0]) for row in rows]
            return [
                {"id": s["id"], "title": s["scenario"]["title"], "name": s["character"]["name"]}
                for s in states
            ]
        finally:
            await db.close()

    @staticmethod
    async def _duplicate(
        db: psycopg.AsyncConnection[tuple[object, ...]], cid: str, command_id: str, text: str
    ) -> Campaign | None:
        cursor = await db.execute(
            "SELECT payload_hash, state_after FROM command_log WHERE campaign=%s AND command_id=%s",
            (cid, command_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        if row[0] != payload_digest({"input": text}):
            raise ConflictError("Request ID already used for different input")
        return AsyncPostgresStore._campaign(row[1])

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
        resolve: Callable[[Campaign], Event],
        *,
        actor_id: str = "player",
    ) -> TurnResult:
        db = await self._connect()
        try:
            async with db.transaction():
                state = await self._read(db, cid, lock=True)
                duplicate = await self._duplicate(db, cid, request_id, text)
                if duplicate is not None:
                    return {"kind": "replayed", "state": duplicate}
                if state["revision"] != revision:
                    raise ConflictError("Campaign changed. Refresh before retrying.")
                event = resolve(state)
                await db.execute(
                    "UPDATE campaigns SET state=%s::jsonb WHERE id=%s",
                    (json.dumps(state), cid),
                )
                await db.execute(
                    """INSERT INTO command_log (
                        campaign, command_id, actor_id, expected_revision,
                        resulting_revision, payload_hash, rules_version,
                        schema_version, event, state_after
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)""",
                    (
                        cid,
                        request_id,
                        actor_id,
                        revision,
                        state["revision"],
                        payload_digest({"input": text}),
                        state["rules"],
                        EVENT_SCHEMA_VERSION,
                        json.dumps(event),
                        json.dumps(state),
                    ),
                )
                if state["revision"] % SNAPSHOT_INTERVAL == 0:
                    await db.execute(
                        "INSERT INTO snapshots (campaign, revision, state) VALUES (%s, %s, %s::jsonb)",
                        (cid, state["revision"], json.dumps(state)),
                    )
                return {"kind": "committed", "state": state, "event": event}
        except (ConflictError, NotFoundError):
            raise
        except psycopg.Error as exc:
            raise StorageError("Unable to commit turn") from exc
        finally:
            await db.close()

    async def save_narration(self, cid: str, revision: int, narration: str) -> None:
        db = await self._connect()
        try:
            async with db.transaction():
                state = await self._read(db, cid, lock=True)
                if state["revision"] == revision:
                    state["messages"][-1]["flavor"] = narration
                    await db.execute(
                        "UPDATE campaigns SET state=%s::jsonb WHERE id=%s",
                        (json.dumps(state), cid),
                    )
        finally:
            await db.close()

    async def history(self, cid: str) -> list[StoredEvent]:
        db = await self._connect()
        try:
            cursor = await db.execute(
                """SELECT command_id, actor_id, expected_revision, resulting_revision,
                          payload_hash, rules_version, schema_version, event, state_after
                   FROM command_log WHERE campaign=%s ORDER BY resulting_revision""",
                (cid,),
            )
            rows = await cursor.fetchall()
            return [self._stored(cid, row) for row in rows]
        finally:
            await db.close()

    @staticmethod
    def _stored(cid: str, row: tuple[object, ...]) -> StoredEvent:
        event_data = validation.mapping(row[7])
        event = Event(
            input=validation.string(event_data["input"]),
            action=validation.event_action(event_data["action"]),
            outcome=validation.string(event_data["outcome"]),
            roll=None if event_data["roll"] is None else validation.roll(event_data["roll"]),
        )
        return StoredEvent(
            campaign_id=cid,
            command_id=validation.string(row[0]),
            actor_id=validation.string(row[1]),
            expected_revision=validation.integer(row[2]),
            resulting_revision=validation.integer(row[3]),
            payload_hash=validation.string(row[4]),
            rules_version=validation.string(row[5]),
            schema_version=validation.integer(row[6]),
            event=event,
            state_after=AsyncPostgresStore._campaign(row[8]),
        )

    async def replay(self, cid: str, revision: int | None = None) -> Campaign:
        db = await self._connect()
        try:
            maximum = revision if revision is not None else 2**63 - 1
            cursor = await db.execute(
                """SELECT revision, state FROM snapshots
                   WHERE campaign=%s AND revision<=%s ORDER BY revision DESC LIMIT 1""",
                (cid, maximum),
            )
            snapshot = await cursor.fetchone()
            if snapshot is None:
                raise NotFoundError("Campaign snapshot not found")
            state = self._campaign(snapshot[1])
            cursor = await db.execute(
                """SELECT state_after FROM command_log
                   WHERE campaign=%s AND resulting_revision>%s AND resulting_revision<=%s
                   ORDER BY resulting_revision""",
                (cid, snapshot[0], maximum),
            )
            for row in await cursor.fetchall():
                state = self._campaign(row[0])
            return state
        finally:
            await db.close()
