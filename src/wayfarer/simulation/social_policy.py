"""Opt-in server-authored v2 policy; frozen v1 scenario documents are unchanged."""

from pydantic import Field

from wayfarer.simulation.actions import ActionRules
from wayfarer.simulation.npcs import NPCSocialRules


class SocialActionRules(ActionRules):
    npcs: NPCSocialRules | None = Field(default=None, exclude=True)
