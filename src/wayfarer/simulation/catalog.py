"""Additive scenario-authoring contracts, separate from frozen play API v1."""

from typing import Literal

from pydantic import Field

from wayfarer.simulation.resources import Id, Record
from wayfarer.simulation.scenario_document import (
    DocumentReport,
    DraftRevision,
    PregeneratedCharacter,
    PublishedRevision,
)


class CatalogRevision(Record):
    draft: DraftRevision
    published: PublishedRevision | None = None


class CatalogEntry(Record):
    id: Id
    owner_id: Id
    version: int = Field(ge=1)
    archived: bool = False
    revisions: tuple[CatalogRevision, ...]


class CatalogCommand(Record):
    id: Id
    operation: Literal["create", "save", "import", "duplicate", "validate", "publish", "archive"]
    expected_version: int = Field(default=0, ge=0)
    content_json: str | None = Field(default=None, max_length=2_000_000)
    revision: int | None = Field(default=None, ge=1)
    party: tuple[PregeneratedCharacter, ...] | None = None


class InstantiateRevision(Record):
    id: Id
    revision: int = Field(ge=1)
    party: tuple[PregeneratedCharacter, ...] | None = None


class AssistScenario(Record):
    """Start one bounded provider proposal against an immutable saved revision."""

    id: Id
    expected_version: int = Field(ge=1)
    revision: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)
    section: Literal["all", "public", "graph", "party", "gm_notes"] = "all"


class CancelGeneration(Record):
    id: Id


class GenerationJob(Record):
    id: Id
    scenario_id: Id
    owner_id: Id
    base_revision: int = Field(ge=1)
    base_version: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=4000)
    section: Literal["all", "public", "graph", "party", "gm_notes"]
    status: Literal["queued", "running", "needs_review", "failed", "cancelled"]
    proposal_json: str | None = Field(default=None, max_length=2_000_000)
    report: DocumentReport | None = None
    error: str | None = Field(default=None, max_length=1000)


class CatalogSummary(Record):
    id: Id
    owner_id: Id
    version: int
    archived: bool
    revision: int
    published: bool
    title: str
    summary: str
    status: Literal["invalid", "needs-party", "playable"]


class RevisionView(Record):
    entry: CatalogSummary
    revision: CatalogRevision
    current_report: DocumentReport
    generation_jobs: tuple[GenerationJob, ...] = ()
