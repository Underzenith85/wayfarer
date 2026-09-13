"""One worker for every multi-step operation (#640).

Four mechanisms used to exist for one shape: a loop inside a request, a KV row
plus a bare task, a job table plus another bare task, and the provider job
worker. They are one here. A `ProcessKind` says how its own work advances; the
worker owns everything that is the same for all of them — the bounded slots, the
timeout, the durable state under version CAS, and the restart story.

The worker holds no kind branching. It asks the registered kind for the next
`Step`, submits whatever commands that step names through the pipeline, runs
whatever provider work it names inside the bounds, and writes the state the kind
returned. A process therefore never touches the store or the campaign lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from wayfarer.contracts import Campaign
from wayfarer.errors import (
    ConflictError,
    ProviderError,
    ProviderOutputError,
    ProviderRequestError,
    ProviderTimeoutError,
    ValidationError,
    provider_diagnostic,
)
from wayfarer.orchestration.entropy import CommandBoundary
from wayfarer.orchestration.pipeline import CommandPlan, submit
from wayfarer.persistence.processes import Process, ProcessStore

# A provider call, or any other awaitable work a step wants run inside the bounds.
Job = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class ProcessCommand:
    """One write a process asks for. It goes through the pipeline like any other."""

    play: CommandBoundary
    cid: str
    plan: CommandPlan[object]
    principal_id: str
    authorize: Callable[[Campaign], None] | None = None


@dataclass(frozen=True)
class Step:
    """What one advance of a process asks for.

    A step names the state its kind wants saved, the commands to submit before
    saving it, and at most one piece of work whose result feeds the next step.
    `done` ends the process with a result; `wait` parks it for an outside event.
    """

    state: str = "{}"
    commands: tuple[ProcessCommand, ...] = ()
    job: Job | None = None
    done: bool = False
    wait: bool = False
    result: str | None = None


@dataclass(frozen=True)
class Identity:
    """What makes two submissions the same process, and who may read its result."""

    id: str
    scope: str
    principal_id: str
    actor_id: str
    key: str = ""
    input_digest: str = ""


@dataclass(frozen=True)
class ProcessKind:
    """A registered multi-step operation: how it is identified and how it advances."""

    name: str
    identity: Callable[[object], Identity]
    step: Callable[[Process, object], Awaitable[Step]]
    # The director's turn is the longest; no kind may loop without bound.
    steps: int = 8
    # What a retry of this identity does. "answer" returns the stored result, which
    # is what a provider call's retry wants. "resume" picks a failed process back
    # up. "repeat" re-enters whatever its state, for work whose own terminal step
    # answers from live state rather than from the row.
    retry: Literal["answer", "resume", "repeat"] = "answer"


@dataclass
class ProcessRegistry:
    """The registered kinds and the one worker that advances all of them."""

    store: ProcessStore
    kinds: dict[str, ProcessKind] = field(default_factory=dict)
    partition: str = "default"
    timeout: float = 300
    slots: int = 8

    def __post_init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[Process]] = {}
        # A caller awaiting a process it started sees the failure that stopped it,
        # not the row's summary. A restarted worker has only the row.
        self.failures: dict[str, BaseException] = {}
        self._slots = asyncio.Semaphore(self.slots)
        self._started = False
        self._starting = asyncio.Lock()

    def register(self, kind: ProcessKind) -> ProcessKind:
        self.kinds[kind.name] = kind
        return kind

    def kind(self, name: str) -> ProcessKind:
        registered = self.kinds.get(name)
        if registered is None:
            raise ValidationError("Unknown process kind")
        return registered

    async def start(self) -> None:
        async with self._starting:
            if not self._started:
                await self.store.recover(self.partition)
                self._started = True

    async def run(self, name: str, value: object) -> Process:
        """Claim this kind's identity for the input, and advance it in the background."""
        await self.start()
        kind = self.kind(name)
        identity = kind.identity(value)
        process = await self.store.create(
            Process(
                id=identity.id,
                kind=kind.name,
                scope=identity.scope,
                principal_id=identity.principal_id,
                actor_id=identity.actor_id,
                partition=self.partition,
                key=identity.key,
                input_digest=identity.input_digest,
            )
        )
        if (process.status == "failed" and kind.retry in ("resume", "repeat")) or (
            process.status == "succeeded" and kind.retry == "repeat"
        ):
            self.failures.pop(process.id, None)
            process = await self.store.resume(process.id)
        if process.status == "queued" and process.id not in self.tasks:
            task = asyncio.create_task(self._advance(kind, process, value))
            self.tasks[process.id] = task

            def finished(task: asyncio.Task[Process]) -> None:
                self.tasks.pop(process.id, None)
                if not task.cancelled():
                    task.exception()

            task.add_done_callback(finished)
        return process

    async def _advance(self, kind: ProcessKind, process: Process, value: object) -> Process:
        try:
            process = await self.store.advance(
                process.model_copy(update={"status": "running"}),
                expected_version=process.version,
            )
        except ConflictError:
            return await self.store.read(process.id)
        try:
            async with asyncio.timeout(self.timeout):
                return await self._steps(kind, process, value)
        except asyncio.CancelledError:
            return await self._fail(process, "worker_stopped")
        except ProviderError as exc:
            diagnostic = provider_diagnostic(exc)
            self.failures[process.id] = exc
            return await self._fail(process, diagnostic.code, stage=exc.stage)
        except Exception as exc:
            self.failures[process.id] = exc
            return await self._fail(process, "process_failed")

    async def _steps(self, kind: ProcessKind, process: Process, value: object) -> Process:
        for _ in range(kind.steps):
            step = await kind.step(process, value)
            for command in step.commands:
                await submit(
                    command.play,
                    command.cid,
                    command.plan,
                    principal_id=command.principal_id,
                    authorize=command.authorize,
                )
            status = "succeeded" if step.done else "waiting" if step.wait else "running"
            process = await self.store.advance(
                process.model_copy(
                    update={
                        "state_json": step.state,
                        "status": status,
                        "result_json": step.result,
                    }
                ),
                expected_version=process.version,
            )
            if step.done or step.wait:
                return process
            # A step with work feeds its result forward; one without re-enters on
            # the same input, which is how a phase loop advances.
            value = value if step.job is None else await self._work(step.job)
        return await self._fail(process, "process_exhausted")

    async def _work(self, job: Job) -> str:
        async with self._slots:
            return await job()

    async def _fail(self, process: Process, error: str, *, stage: str | None = None) -> Process:
        try:
            return await self.store.advance(
                process.model_copy(
                    update={"status": "failed", "error": error, "error_stage": stage}
                ),
                expected_version=process.version,
            )
        except ConflictError:
            # A restarted worker has already published the terminal failure.
            return await self.store.read(process.id)

    async def result(self, process: Process) -> str:
        """The process's own result, waiting for this worker's task if it holds one."""
        task = self.tasks.get(process.id)
        if task is not None:
            await asyncio.shield(task)
        failure = self.failures.pop(process.id, None)
        if failure is not None:
            raise failure
        latest = await self.store.read(process.id)
        if latest.status != "succeeded" or latest.result_json is None:
            error_type = {
                "provider_timeout": ProviderTimeoutError,
                "invalid_provider_output": ProviderOutputError,
                "provider_request_rejected": ProviderRequestError,
            }.get(latest.error or "", ProviderError)
            error = error_type(latest.error or "Process is pending")
            error.code = latest.error or "provider_unavailable"
            error.stage = latest.error_stage
            raise error
        return latest.result_json

    async def status(self, process_id: str) -> Process:
        return await self.store.read(process_id)

    async def drain(self) -> None:
        await asyncio.gather(*tuple(self.tasks.values()))
        self.failures.clear()

    async def close(self) -> None:
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
