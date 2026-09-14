"""Scenario authoring as its own process kind (#640).

A character draft is one provider call: ask, validate, save. Scenario authoring is
not. It proposes, validates against the runtime, repairs within a budget and keeps
the best draft it has seen, and that draft is two megabytes of scenario. So the two
are built separately: the draft stays a one-shot provider call, and authoring is a
kind of its own whose durable state is the authoring row — the process row carries
only the lifecycle, and a restart resumes from the row's best draft.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer.errors import ProviderError, ValidationError
from wayfarer.orchestration.processes import Identity, ProcessKind, Step
from wayfarer.persistence.processes import Process

if TYPE_CHECKING:
    from wayfarer.orchestration.catalog import ScenarioCatalog
    from wayfarer.orchestration.providers import Orchestrator


@dataclass(frozen=True)
class AuthoringWork:
    """One scenario authoring job, as the process that drives it sees it."""

    catalog: ScenarioCatalog
    llm: Orchestrator
    principal: str
    job_id: str


def authoring_identity(value: object) -> Identity:
    if not isinstance(value, AuthoringWork):
        raise ValidationError("Scenario authoring requires a generation job")
    return Identity(
        id="scenario-authoring:" + value.job_id,
        scope="scenario-authoring:" + value.principal,
        principal_id=value.principal,
        actor_id=value.principal,
        key=value.job_id,
        input_digest=value.job_id,
    )


async def authoring_step(process: Process, value: object) -> Step:
    """Run the job's own attempt budget, then finish with the status it reached."""
    if isinstance(value, AuthoringWork):

        async def run() -> str:
            try:
                job = await value.catalog.run_generation_job(
                    value.principal, value.job_id, value.llm
                )
            except (ProviderError, ValidationError, ValueError) as exc:
                # The author reads the authoring row, so the diagnostic lands there
                # as well as on the process this worker is running.
                failed = await value.catalog.fail_generation_job(value.principal, value.job_id, exc)
                return "failed" if failed is None else failed.status
            return job.status

        return Step(state=json.dumps({"stage": "authoring"}), job=run)
    return Step(state=json.dumps({"stage": "complete"}), done=True, result=str(value))


SCENARIO_AUTHORING = ProcessKind(
    name="scenario_authoring",
    identity=authoring_identity,
    step=authoring_step,
    steps=2,
    # A retry picks an interrupted job back up; the row still holds its best draft.
    retry="resume",
)
