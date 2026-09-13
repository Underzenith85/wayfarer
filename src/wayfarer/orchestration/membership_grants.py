"""Seating a principal in a campaign, as a command family like any other (#639).

A redeemed invitation used to be the one place a transport wrote play state by
hand. It is a command: it names the principal it seats, it commits through the
pipeline, and its receipt is the record that the seat was granted once.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.models import Id, Record
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService


class MembershipGrant(Record):
    kind: Literal["grant_membership"] = "grant_membership"
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    invitation_id: Id
    role: Literal["gm", "player"]


class MembershipService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, command: MembershipGrant, *, instant: CommandInstant | None = None
    ) -> CommandPlan[None]:
        """What a redeemed invitation writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {
                "operation": "v1-invitation",
                "invitation": command.invitation_id,
                "principal": command.actor_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            play = self.play.for_campaign(campaign)
            state = play._load(campaign)
            if not any(m.principal_id == command.actor_id for m in state.members):
                # A grant seats a principal; it never assigns actors or promotes a role.
                seat = CampaignMember(principal_id=command.actor_id, role=command.role)
                state = state.model_copy(update={"members": state.members + (seat,)})
            revision = state.revision + 1
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            play.commit(campaign, state)
            return CommandReceipt(action="v1-membership", outcome="Membership granted")

        async def outcome(campaign: Campaign) -> None:
            return None

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(ActsAs(command.actor_id),),
            rng=self.play.rng,
            instant=instant,
        )

    async def execute(
        self, cid: str, command: MembershipGrant, *, principal_id: str, instant: CommandInstant
    ) -> None:
        await submit(self.play, cid, self.plan(command, instant=instant), principal_id=principal_id)
