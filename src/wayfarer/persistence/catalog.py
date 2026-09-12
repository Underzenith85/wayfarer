"""Transactional scenario aggregates and durable receipts on the configured database.

Catalog state never enters campaign listing. PostgreSQL serializes commands with a
transaction advisory lock; SQLite uses BEGIN IMMEDIATE, including first creates.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import aiosqlite
import psycopg

from wayfarer.engine.simulation.campaign.scenario_catalog import CatalogEntry, ScenarioGenerationJob
from wayfarer.errors import ConflictError, NotFoundError, StorageError
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


class Connection:
    def __init__(
        self, db: aiosqlite.Connection | psycopg.AsyncConnection[tuple[object, ...]]
    ) -> None:
        self.db = db

    async def query(self, sql: str, args: tuple[str, ...] = ()) -> list[tuple[object, ...]]:
        if isinstance(self.db, aiosqlite.Connection):
            cursor = await self.db.execute(sql, args)
            rows = await cursor.fetchall()
            await cursor.close()
            return [tuple(row) for row in rows]
        cursor_pg = await self.db.execute(sql.replace("?", "%s"), args)
        return await cursor_pg.fetchall() if cursor_pg.description else []


class CatalogStore:
    def __init__(self, store: AsyncSQLiteStore | AsyncPostgresStore) -> None:
        self.store = store

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Connection]:
        db = await self.store._connect()
        try:
            connection = Connection(db)
            # Serialize schema initialization as well as creates and receipts across processes.
            if isinstance(db, aiosqlite.Connection):
                await db.execute("BEGIN IMMEDIATE")
            else:
                await db.execute("SELECT pg_advisory_xact_lock(830083)")
            await connection.query("""CREATE TABLE IF NOT EXISTS scenario_catalog (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL)""")
            await connection.query("""CREATE TABLE IF NOT EXISTS scenario_receipts (
                principal TEXT NOT NULL, command_id TEXT NOT NULL, payload TEXT NOT NULL,
                state_after TEXT NOT NULL, PRIMARY KEY(principal, command_id))""")
            await connection.query("""CREATE TABLE IF NOT EXISTS scenario_generation_jobs (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, state TEXT NOT NULL)""")
            yield connection
            await db.commit()
        except (aiosqlite.Error, psycopg.Error) as exc:
            await db.rollback()
            raise StorageError("Unable to persist scenario catalog") from exc
        except BaseException:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def read(self, cid: str) -> CatalogEntry:
        async with self.transaction() as db:
            rows = await db.query("SELECT state FROM scenario_catalog WHERE id=?", (cid,))
            if not rows:
                raise NotFoundError("Scenario not found")
            return CatalogEntry.model_validate_json(str(rows[0][0]))

    async def listing(self, owner: str) -> tuple[CatalogEntry, ...]:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_catalog WHERE owner_id=? ORDER BY id", (owner,)
            )
            return tuple(CatalogEntry.model_validate_json(str(row[0])) for row in rows)

    async def create_job(self, job: ScenarioGenerationJob) -> ScenarioGenerationJob:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE id=?", (job.id,)
            )
            if rows:
                existing = ScenarioGenerationJob.model_validate_json(str(rows[0][0]))
                if existing.owner_id != job.owner_id or existing.request != job.request:
                    raise ConflictError("Generation job identity already used")
                return existing
            await db.query(
                "INSERT INTO scenario_generation_jobs VALUES (?, ?, ?)",
                (job.id, job.owner_id, job.model_dump_json()),
            )
            return job

    async def read_job(self, job_id: str, owner: str) -> ScenarioGenerationJob:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE id=? AND owner_id=?",
                (job_id, owner),
            )
            if not rows:
                raise NotFoundError("Generation job not found")
            return ScenarioGenerationJob.model_validate_json(str(rows[0][0]))

    async def update_job(
        self, job: ScenarioGenerationJob, expected_version: int
    ) -> ScenarioGenerationJob:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE id=? AND owner_id=?",
                (job.id, job.owner_id),
            )
            if not rows:
                raise NotFoundError("Generation job not found")
            previous = ScenarioGenerationJob.model_validate_json(str(rows[0][0]))
            if previous.version != expected_version:
                raise ConflictError("Generation job changed")
            result = job.model_copy(update={"version": expected_version + 1})
            await db.query(
                "UPDATE scenario_generation_jobs SET state=? WHERE id=? AND owner_id=?",
                (result.model_dump_json(), result.id, result.owner_id),
            )
            return result

    async def commit(
        self,
        cid: str,
        principal: str,
        command: str,
        payload: str,
        resolve: Callable[[CatalogEntry | None], CatalogEntry],
    ) -> CatalogEntry:
        async with self.transaction() as db:
            receipts = await db.query(
                "SELECT payload, state_after FROM scenario_receipts WHERE principal=? AND command_id=?",
                (principal, command),
            )
            if receipts:
                if receipts[0][0] != payload:
                    raise ConflictError("Scenario command ID already used for different input")
                return CatalogEntry.model_validate_json(str(receipts[0][1]))
            rows = await db.query("SELECT state FROM scenario_catalog WHERE id=?", (cid,))
            previous = CatalogEntry.model_validate_json(str(rows[0][0])) if rows else None
            result = resolve(previous)
            await db.query(
                """INSERT INTO scenario_catalog (id, owner_id, state) VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET state=excluded.state""",
                (cid, result.owner_id, result.model_dump_json()),
            )
            await db.query(
                "INSERT INTO scenario_receipts VALUES (?, ?, ?, ?)",
                (principal, command, payload, result.model_dump_json()),
            )
            return result
