"""Durable actor-scoped turn cursors; no generated mechanical authority."""

from typing import Literal

from pydantic import Field

from wayfarer.simulation.resources import Id, Record


class DirectorTurn(Record):
    id: Id
    actor_id: Id
    principal_id: Id
    session_id: str
    text: str = Field(min_length=1, max_length=4000)
    phase: Literal["interpretation", "resolution", "narration", "complete", "clarification"] = (
        "interpretation"
    )
    request_json: str | None = None
    command_json: str | None = None
    trace_json: str | None = None
    outcome_json: str | None = None
    narration: str = ""
    narration_available: bool = False
    committed: bool = False


class AuthorDraft(Record):
    id: Id
    owner_id: Id
    actor_id: Id
    kind: Literal["character", "scenario"]
    revision: int = Field(ge=1)
    content_json: str
    previous_json: str | None = None
    approval_json: str | None = None
    activated_revision: int | None = None
