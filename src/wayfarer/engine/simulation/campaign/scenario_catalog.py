"""Additive scenario-authoring contracts, separate from frozen play API v1."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.campaign.scenario_document import (
    DocumentReport,
    DraftRevision,
    PregeneratedCharacter,
)
from wayfarer.engine.simulation.campaign.scenario_loading import PublishedRevision
from wayfarer.engine.simulation.campaign.studio import GenerationBrief
from wayfarer.models import Id, Record


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


GenerationStatus = Literal["queued", "running", "needs_review", "succeeded", "failed", "cancelled"]
GenerationSection = Literal["all", "brief", "opening", "world", "objectives", "characters"]


class ScenarioGenerationRequest(Record):
    """A bounded, non-authoritative request for an editable scenario proposal."""

    id: Id
    brief: GenerationBrief
    instructions: str = Field(default="", max_length=3200)
    party_capabilities: tuple[Id, ...] = Field(default=(), max_length=30)
    section: GenerationSection = "all"
    source_json: str | None = Field(default=None, max_length=2_000_000)
    source_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    attempts: int = Field(default=2, ge=1, le=3)


class ScenarioGenerationJob(Record):
    id: Id
    owner_id: Id
    version: int = Field(ge=1)
    status: GenerationStatus
    request: ScenarioGenerationRequest
    proposal_json: str | None = Field(default=None, max_length=2_000_000)
    report: DocumentReport | None = None
    error_code: str | None = None
    error_message: str | None = None
