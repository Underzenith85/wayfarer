"""Audience-scoped disclosure of compiled character identities."""

from typing import Literal

from wayfarer.engine.character.construction import Identity
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.events import (
    ActorAudience,
    CampaignAudience,
    EventAudience,
    audience_visible,
)
from wayfarer.models import Record


class IdentityRecorded(Record):
    kind: Literal["identity.recorded"] = "identity.recorded"
    audience: EventAudience
    actor_id: str
    identity_id: str
    identity_kind: Literal["legal", "alternate", "secret"]
    name: str


def identity_events(
    actor_id: str, identities: tuple[Identity, ...]
) -> tuple[IdentityRecorded, ...]:
    """Publish legal names campaign-wide and keep other identities allowlisted."""
    result = []
    for identity in identities:
        audience: EventAudience = (
            CampaignAudience()
            if identity.kind == "legal"
            else ActorAudience(actor_ids=tuple(sorted({actor_id, *identity.known_actor_ids})))
        )
        result.append(
            IdentityRecorded(
                audience=audience,
                actor_id=actor_id,
                identity_id=identity.id,
                identity_kind=identity.kind,
                name=identity.name,
            )
        )
    return tuple(result)


def visible_identity_events(
    events: tuple[IdentityRecorded, ...], member: CampaignMember
) -> tuple[IdentityRecorded, ...]:
    return tuple(event for event in events if audience_visible(event.audience, member))
