"""Campaign membership and actor-control contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.models import Id, Record


class CampaignMember(Record):
    principal_id: Id
    role: Literal["gm", "player", "spectator"]
    actor_ids: tuple[Id, ...] = Field(default=(), max_length=100)


class StreamEvent(Record):
    cursor: int = Field(ge=1)
    command_id: str
    actor_id: str
    action: str
    outcome: str
    projection: dict[str, object]
