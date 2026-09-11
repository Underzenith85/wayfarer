"""Bounded provider workers; terminal results are published atomically to the outbox."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Literal
from weakref import ReferenceType, WeakKeyDictionary, ref

from wayfarer.errors import (
    ConflictError,
    ProviderError,
    ProviderOutputError,
    ProviderRequestError,
    ProviderTimeoutError,
    provider_diagnostic,
)
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.catalog import CatalogStore
from wayfarer.persistence.jobs import JobStore, ProviderJob


class ProviderJobs:
    def __init__(self, store: Store, *, partition: str = "default") -> None:
        self.store = JobStore(CatalogStore(store))
        self.partition = partition
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(8)
        self._started = False
        self._starting = asyncio.Lock()

    async def start(self) -> None:
        async with self._starting:
            if not self._started:
                await self.store.recover(self.partition)
                self._started = True

    async def submit(
        self,
        *,
        cid: str,
        revision: int,
        principal: str,
        actor: str,
        kind: Literal["narration", "proposal"],
        key: str,
        request_json: str,
        run: Callable[[], Awaitable[str]],
    ) -> ProviderJob:
        await self.start()
        identity = json.dumps(
            (cid, revision, principal, actor, kind, "narration" if kind == "narration" else key)
        )
        job = await self.store.create(
            ProviderJob(
                id=hashlib.sha256(identity.encode()).hexdigest(),
                campaign_id=cid,
                revision=revision,
                principal_id=principal,
                actor_id=actor,
                kind=kind,
                key=key,
                partition=self.partition,
                request_json=request_json,
            )
        )
        if job.status == "queued" and job.id not in self.tasks:
            task = asyncio.create_task(self._run(job, run))
            self.tasks[job.id] = task

            def done(task: asyncio.Task[None]) -> None:
                self.tasks.pop(job.id, None)
                if not task.cancelled():
                    task.exception()

            task.add_done_callback(done)
        return job

    async def _run(self, job: ProviderJob, run: Callable[[], Awaitable[str]]) -> None:
        try:
            job = await self.store.update(
                job.model_copy(update={"status": "running"}), expected_version=job.version
            )
        except ConflictError:
            return
        try:
            async with self._slots, asyncio.timeout(300):
                result = await run()
            completed = ProviderJob.model_validate(
                job.model_copy(update={"status": "succeeded", "result_json": result})
            )
        except asyncio.CancelledError:
            completed = job.model_copy(update={"status": "failed", "error": "worker_stopped"})
        except ProviderError as exc:
            completed = job.model_copy(
                update={
                    "status": "failed",
                    "error": provider_diagnostic(exc).code,
                    "error_stage": exc.stage,
                }
            )
        except Exception:
            completed = job.model_copy(update={"status": "failed", "error": "provider_failed"})
        try:
            await self.store.update(completed, expected_version=job.version)
        except ConflictError:
            pass  # A restarted worker has already published the terminal failure.

    async def result(self, job: ProviderJob) -> str:
        task = self.tasks.get(job.id)
        if task is not None:
            await asyncio.shield(task)
        latest = await self.store.read(job.id)
        if latest.status != "succeeded" or latest.result_json is None:
            error_type = {
                "provider_timeout": ProviderTimeoutError,
                "invalid_provider_output": ProviderOutputError,
                "provider_request_rejected": ProviderRequestError,
            }.get(latest.error or "", ProviderError)
            error = error_type(latest.error or "Provider job is pending")
            error.code = latest.error or "provider_unavailable"
            error.stage = latest.error_stage
            raise error
        return latest.result_json

    async def drain(self) -> None:
        await asyncio.gather(*tuple(self.tasks.values()))

    async def close(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_RUNNERS: WeakKeyDictionary[Store, ReferenceType[ProviderJobs]] = WeakKeyDictionary()


def jobs_for(store: Store) -> ProviderJobs:
    saved = _RUNNERS.get(store)
    jobs = saved() if saved is not None else None
    if jobs is None:
        jobs = ProviderJobs(store)
        _RUNNERS[store] = ref(jobs)
    return jobs
