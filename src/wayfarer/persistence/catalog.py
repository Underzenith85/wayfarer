"""Transactional scenario aggregates and durable receipts on the configured database.

Catalog state never enters campaign listing. PostgreSQL serializes commands with a
transaction advisory lock; SQLite uses BEGIN IMMEDIATE, including first creates.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import aiosqlite
import psycopg

from wayfarer.errors import ConflictError, NotFoundError, StorageError
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.simulation.catalog import CatalogEntry, GenerationJob


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
                scenario_id TEXT NOT NULL, job_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY(scenario_id, job_id))""")
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

    async def create_job(self, job: GenerationJob) -> GenerationJob:
        """Idempotently persist a provider job without changing scenario revision/version."""
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE scenario_id=? AND job_id=?",
                (job.scenario_id, job.id),
            )
            if rows:
                previous = GenerationJob.model_validate_json(str(rows[0][0]))
                comparable = previous.model_copy(
                    update={"status": job.status, "proposal_json": None, "report": None, "error": None}
                )
                expected = job.model_copy(update={"proposal_json": None, "report": None, "error": None})
                if comparable != expected:
                    raise ConflictError("Generation job ID already used for different input")
                return previous
            await db.query(
                "INSERT INTO scenario_generation_jobs VALUES (?, ?, ?, ?)",
                (job.scenario_id, job.id, job.owner_id, job.model_dump_json()),
            )
            return job

    async def read_job(self, cid: str, job_id: str, owner: str) -> GenerationJob:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE scenario_id=? AND job_id=? AND owner_id=?",
                (cid, job_id, owner),
            )
            if not rows:
                raise NotFoundError("Generation job not found")
            return GenerationJob.model_validate_json(str(rows[0][0]))

    async def jobs(self, cid: str, owner: str) -> tuple[GenerationJob, ...]:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE scenario_id=? AND owner_id=? ORDER BY job_id",
                (cid, owner),
            )
            return tuple(GenerationJob.model_validate_json(str(row[0])) for row in rows)

    async def update_job(
        self,
        cid: str,
        job_id: str,
        owner: str,
        resolve: Callable[[GenerationJob], GenerationJob],
    ) -> GenerationJob:
        async with self.transaction() as db:
            rows = await db.query(
                "SELECT state FROM scenario_generation_jobs WHERE scenario_id=? AND job_id=? AND owner_id=?",
                (cid, job_id, owner),
            )
            if not rows:
                raise NotFoundError("Generation job not found")
            previous = GenerationJob.model_validate_json(str(rows[0][0]))
            result = resolve(previous)
            if result.id != previous.id or result.scenario_id != cid or result.owner_id != owner:
                raise ConflictError("Generation job identity cannot change")
            await db.query(
                "UPDATE scenario_generation_jobs SET state=? WHERE scenario_id=? AND job_id=?",
                (result.model_dump_json(), cid, job_id),
            )
            return result
