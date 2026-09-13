"""Durable multi-step work: one row per process, advanced under version CAS (#640).

A process is whatever a registered kind says it is — a director's turn, a
provider call, a scenario generation. This module knows only that it has an
identity, a scope, an opaque state its kind owns, and a version that every
advance must match. Terminal results reach the audience through the same outbox
the provider jobs used.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError
from wayfarer.models import Record
from wayfarer.persistence.catalog import CatalogStore, Connection

ProcessStatus = Literal["queued", "running", "waiting", "succeeded", "failed"]


class Process(Record):
    """One durable unit of multi-step work.

    ``state_json`` belongs to the kind: the store and the worker never read it.
    ``input_digest`` is what the kind considers the same request, so a retry under
    one identity is answered rather than run twice.
    """

    id: str
    kind: str
    scope: str
    # The audience a terminal result is published to, and the partition that owns it.
    principal_id: str
    actor_id: str
    partition: str = "default"
    # What the kind calls this work inside its scope. The worker never reads it;
    # an outbox reader matches on it to find the result it asked for.
    key: str = ""
    input_digest: str = ""
    state_json: str = Field(default="{}", max_length=256000, repr=False)
    status: ProcessStatus = "queued"
    version: int = 0
    result_json: str | None = Field(default=None, max_length=256000, repr=False)
    error: str | None = None
    error_stage: str | None = None


class ProcessStore:
    def __init__(self, catalog: CatalogStore) -> None:
        self.catalog = catalog

    async def create(self, process: Process) -> Process:
        """Claim an identity, or answer the retry that already holds it."""
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM processes WHERE id=?", (process.id,))
            if rows:
                previous = Process.model_validate_json(str(rows[0][0]))
                if previous.input_digest != process.input_digest:
                    raise ConflictError("Process identity reused for different input")
                return previous
            await db.query(
                "INSERT INTO processes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    process.id,
                    process.kind,
                    process.scope,
                    process.principal_id,
                    process.actor_id,
                    process.model_dump_json(),
                ),
            )
            return process

    async def read(self, process_id: str) -> Process:
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM processes WHERE id=?", (process_id,))
            if not rows:
                raise ConflictError("Process is unavailable")
            return Process.model_validate_json(str(rows[0][0]))

    async def advance(self, process: Process, *, expected_version: int) -> Process:
        """Write the next state of a process, or refuse a stale writer."""
        process = Process.model_validate(process)
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM processes WHERE id=?", (process.id,))
            if not rows:
                raise ConflictError("Process is unavailable")
            previous = Process.model_validate_json(str(rows[0][0]))
            if previous.version != expected_version or previous.status in ("succeeded", "failed"):
                raise ConflictError("Process changed")
            saved = process.model_copy(update={"version": expected_version + 1})
            await db.query(
                "UPDATE processes SET state=? WHERE id=?", (saved.model_dump_json(), process.id)
            )
            if saved.status in ("succeeded", "failed"):
                await self._publish(db, saved)
            return saved

    async def resume(self, process_id: str) -> Process:
        """Hand a terminal process back to the worker, for the kinds that retry.

        A provider call's result is its answer, success or failure. A turn is
        unfinished work until its own phases say otherwise, and its terminal phases
        answer from the campaign rather than from this row. The kind decides which,
        and this is how one is picked back up.
        """
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM processes WHERE id=?", (process_id,))
            if not rows:
                raise ConflictError("Process is unavailable")
            previous = Process.model_validate_json(str(rows[0][0]))
            if previous.status not in ("failed", "succeeded"):
                return previous
            resumed = previous.model_copy(
                update={
                    "status": "queued",
                    "error": None,
                    "error_stage": None,
                    "result_json": None,
                    "version": previous.version + 1,
                }
            )
            await db.query(
                "UPDATE processes SET state=? WHERE id=?", (resumed.model_dump_json(), process_id)
            )
            return resumed

    async def recover(self, partition: str) -> None:
        """A partition has one worker; restart fails its interrupted work explicitly."""
        async with self.catalog.transaction() as db:
            for row in await db.query("SELECT state FROM processes"):
                process = Process.model_validate_json(str(row[0]))
                if process.partition != partition or process.status not in (
                    "queued",
                    "running",
                    "waiting",
                ):
                    continue
                failed = process.model_copy(
                    update={
                        "status": "failed",
                        "error": "worker_restarted",
                        "version": process.version + 1,
                    }
                )
                await db.query(
                    "UPDATE processes SET state=? WHERE id=?", (failed.model_dump_json(), failed.id)
                )
                await self._publish(db, failed)

    async def outbox(self, cid: str, principal: str, actor: str) -> tuple[Process, ...]:
        async with self.catalog.transaction() as db:
            rows = await db.query(
                """SELECT p.state FROM provider_outbox o JOIN processes p ON p.id=o.job_id
                WHERE o.campaign_id=? AND o.principal_id=? AND o.actor_id=? ORDER BY o.job_id""",
                (cid, principal, actor),
            )
            return tuple(Process.model_validate_json(str(row[0])) for row in rows)

    @staticmethod
    async def _publish(db: Connection, process: Process) -> None:
        """A terminal process is visible to its audience through the shared outbox."""
        await db.query(
            "INSERT INTO provider_outbox VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO NOTHING",
            (process.id, process.scope, process.principal_id, process.actor_id),
        )
