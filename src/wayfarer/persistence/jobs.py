"""Durable provider work and an audience-scoped result outbox."""

from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError
from wayfarer.models import Record
from wayfarer.persistence.catalog import CatalogStore


class ProviderJob(Record):
    id: str
    campaign_id: str
    revision: int = Field(ge=0)
    principal_id: str
    actor_id: str
    kind: Literal["narration", "proposal"]
    partition: str = "default"
    key: str = ""
    request_json: str = Field(max_length=256000, repr=False)
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    version: int = 0
    result_json: str | None = Field(default=None, max_length=256000, repr=False)
    error: str | None = None
    error_stage: str | None = None


class JobStore:
    def __init__(self, catalog: CatalogStore) -> None:
        self.catalog = catalog

    async def _schema(self) -> None:
        async with self.catalog.transaction() as db:
            await db.query("""CREATE TABLE IF NOT EXISTS provider_jobs (
                id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, principal_id TEXT NOT NULL,
                actor_id TEXT NOT NULL, state TEXT NOT NULL)""")
            await db.query("""CREATE TABLE IF NOT EXISTS provider_outbox (
                job_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                principal_id TEXT NOT NULL, actor_id TEXT NOT NULL)""")

    async def recover(self, partition: str) -> None:
        """A partition has one worker; restart explicitly fails its interrupted work."""
        await self._schema()
        async with self.catalog.transaction() as db:
            for row in await db.query("SELECT state FROM provider_jobs"):
                job = ProviderJob.model_validate_json(str(row[0]))
                if job.partition == partition and job.status in ("queued", "running"):
                    failed = job.model_copy(
                        update={
                            "status": "failed",
                            "error": "worker_restarted",
                            "version": job.version + 1,
                        }
                    )
                    await db.query(
                        "UPDATE provider_jobs SET state=? WHERE id=?",
                        (failed.model_dump_json(), job.id),
                    )
                    await db.query(
                        "INSERT INTO provider_outbox VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO NOTHING",
                        (job.id, job.campaign_id, job.principal_id, job.actor_id),
                    )

    async def create(self, job: ProviderJob) -> ProviderJob:
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM provider_jobs WHERE id=?", (job.id,))
            if rows:
                previous = ProviderJob.model_validate_json(str(rows[0][0]))
                if job.kind == "narration":
                    return previous  # One narration per campaign revision and audience.
                if previous.model_dump(
                    exclude={"status", "version", "result_json", "error", "error_stage"}
                ) != job.model_dump(
                    exclude={"status", "version", "result_json", "error", "error_stage"}
                ):
                    raise ConflictError("Provider job identity reused for different input")
                return previous
            await db.query(
                "INSERT INTO provider_jobs VALUES (?, ?, ?, ?, ?)",
                (job.id, job.campaign_id, job.principal_id, job.actor_id, job.model_dump_json()),
            )
            return job

    async def read(self, job_id: str) -> ProviderJob:
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM provider_jobs WHERE id=?", (job_id,))
            if not rows:
                raise ConflictError("Provider job is unavailable")
            return ProviderJob.model_validate_json(str(rows[0][0]))

    async def update(self, job: ProviderJob, *, expected_version: int) -> ProviderJob:
        job = ProviderJob.model_validate(job)
        async with self.catalog.transaction() as db:
            rows = await db.query("SELECT state FROM provider_jobs WHERE id=?", (job.id,))
            previous = ProviderJob.model_validate_json(str(rows[0][0]))
            if previous.version != expected_version or previous.status in ("succeeded", "failed"):
                raise ConflictError("Provider job changed")
            saved = job.model_copy(update={"version": expected_version + 1})
            await db.query(
                "UPDATE provider_jobs SET state=? WHERE id=?", (saved.model_dump_json(), job.id)
            )
            if saved.status in ("succeeded", "failed"):
                await db.query(
                    "INSERT INTO provider_outbox VALUES (?, ?, ?, ?) ON CONFLICT(job_id) DO NOTHING",
                    (job.id, job.campaign_id, job.principal_id, job.actor_id),
                )
            return saved

    async def outbox(self, cid: str, principal: str, actor: str) -> tuple[ProviderJob, ...]:
        async with self.catalog.transaction() as db:
            rows = await db.query(
                """SELECT j.state FROM provider_outbox o JOIN provider_jobs j ON j.id=o.job_id
                WHERE o.campaign_id=? AND o.principal_id=? AND o.actor_id=? ORDER BY o.job_id""",
                (cid, principal, actor),
            )
            return tuple(ProviderJob.model_validate_json(str(row[0])) for row in rows)
