"""Strict scenario dispatch and validation of published document snapshots."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.campaign.scenario_document import (
    Digest,
    DocumentReport,
    ScenarioDocument,
    ScenarioDocumentBase,
    ScenarioReference,
)
from wayfarer.engine.simulation.campaign.social_policy import SocialScenarioDocument
from wayfarer.models import Id, Record


def parse_document(source: str) -> ScenarioDocumentBase:
    """Reject unknown versions and ambiguous JSON before strict domain decoding."""
    if len(source) > 2_000_000:
        raise ValueError("Scenario document exceeds the import size limit")

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError(f"Non-finite JSON number: {value}")

    raw = json.loads(source, object_pairs_hook=pairs, parse_constant=constant)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] not in (1, 2)
    ):
        raise ValueError("Unsupported scenario schema_version; explicit migration is required")
    if raw["schema_version"] == 2:
        return SocialScenarioDocument.model_validate_json(source)
    return ScenarioDocument.model_validate_json(source)


class PublishedRevision(Record):
    """Immutable serialized snapshot; derived models cannot mutate the stored content."""

    scenario_id: Id
    revision_id: Id
    revision: int = Field(ge=1)
    content_json: str
    report: DocumentReport

    @model_validator(mode="after")
    def consistent(self) -> PublishedRevision:
        document = parse_document(self.content_json)
        if (
            self.report.status != "playable"
            or self.report.content_digest != document.digest
            or self.report.engine_digest != document.compatibility.engine_digest
            or (self.scenario_id, self.revision_id, self.revision)
            != (document.scenario_id, document.revision_id, document.revision)
            or self.content_json != document.canonical()
        ):
            raise ValueError("Published revision does not match validated canonical content")
        return self


class ScenarioBoundary(Record):
    """Adventure-local revision zero on a monotonic campaign stream."""

    reference: ScenarioReference
    graph_digest: Digest
    runtime_digest: Digest
    command_id: str
    campaign_revision: int = Field(ge=0)
    revision: Literal[0] = 0
    segment: int = Field(ge=0)
    published: PublishedRevision | None = None
