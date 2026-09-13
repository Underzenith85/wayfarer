"""The registry every read resolves through (#637).

A `Projection` says who its output is for and how to build it. HTTP reads, the
live stream, the model's context and the v1 views ask for one by name instead of
loading a checkpoint and filtering it again, so the audience rule has one owner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import NotFoundError
from wayfarer.orchestration.views import campaign_view, player_detail

if TYPE_CHECKING:
    from wayfarer.orchestration.runtime import CampaignRuntime

# Who a projection is built for. `member` means the reader's own role decides.
Audience = Literal["member", "director", "player"]


@dataclass(frozen=True)
class ViewRequest:
    """One read: the campaign's checkpoint, and who is asking."""

    runtime: CampaignRuntime
    state: PlayState
    member: CampaignMember


@dataclass(frozen=True)
class Projection:
    name: str
    audience: Audience
    build: Callable[[ViewRequest], dict[str, object]]

    def permits(self, member: CampaignMember) -> bool:
        if self.audience == "member":
            return True
        return member.role == ("gm" if self.audience == "director" else "player")


def _campaign(request: ViewRequest) -> dict[str, object]:
    view = campaign_view(request.state, request.member, request.runtime.rules.combat)
    if request.member.role != "player":
        return view
    return player_detail(request.runtime, request.state, request.member, view)


def _stream(request: ViewRequest) -> dict[str, object]:
    """The per-event view the live stream carries; never the player's own detail.

    An event carries what changed, not the reader's sheet and offered choices,
    so it stops at the audience-filtered campaign view.
    """
    return campaign_view(request.state, request.member, request.runtime.rules.combat)


PROJECTIONS: tuple[Projection, ...] = (
    Projection(name="campaign", audience="member", build=_campaign),
    Projection(name="stream", audience="member", build=_stream),
)

BY_NAME: dict[str, Projection] = {p.name: p for p in PROJECTIONS}


def projection(name: str) -> Projection:
    registered = BY_NAME.get(name)
    if registered is None:
        raise NotFoundError("Unknown projection")
    return registered


def project(name: str, request: ViewRequest) -> dict[str, object]:
    """Build one registered projection for this reader."""
    registered = projection(name)
    if not registered.permits(request.member):
        raise NotFoundError("Projection is not for this reader")
    return registered.build(request)
