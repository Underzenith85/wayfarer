"""Who a principal is in a campaign, and whether they may act for an actor.

Membership is read before any authorized command runs, so it sits below the
services that ask.  ``CampaignAccess`` keeps the two staticmethods its callers
already use and delegates here.
"""

from __future__ import annotations

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import AuthorizationError, NotFoundError


def member_for(state: PlayState, principal_id: str) -> CampaignMember:
    member = next((item for item in state.members if item.principal_id == principal_id), None)
    if member is None:
        # Avoid disclosing whether an inaccessible campaign exists.
        raise NotFoundError("Campaign not found")
    return member


def require_control(member: CampaignMember, actor_id: str) -> None:
    if not (
        (member.role == "player" and actor_id in member.actor_ids)
        or (member.role == "gm" and actor_id == member.principal_id)
    ):
        raise AuthorizationError("Principal cannot control this actor")
