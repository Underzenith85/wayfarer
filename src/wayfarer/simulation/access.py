"""Campaign membership and actor-control contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


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


def validate_members(state: PlayState) -> None:
    """Each principal appears once and only players control actors, all of them approved."""
    if len({member.principal_id for member in state.members}) != len(state.members):
        raise ValidationError("Duplicate campaign member")
    actor_ids = {actor.actor_id for actor in state.actors}
    if any(
        len(set(member.actor_ids)) != len(member.actor_ids)
        or not set(member.actor_ids) <= actor_ids
        or (member.role != "player" and member.actor_ids)
        for member in state.members
    ):
        raise ValidationError("Invalid campaign actor control")
