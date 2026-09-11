"""Director-only care and panic decisions using the existing campaign CAS ledger."""

import json

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.mechanics.fright import FrightDecision as FrightDecision
from wayfarer.simulation.mechanics.fright import apply_decision


class FrightService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> None:
        try:
            command = FrightDecision.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid fright decision") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        member = CampaignAccess(play)._member(
            play._load(await play.store.read(cid)), authenticated_gm_id
        )
        if member.role != "gm" or authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Fright decisions require director authority")
        payload = json.dumps(
            {
                "operation": "fright-decision",
                "principal": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> Event:
            before = play._load(campaign)
            updated = apply_decision(before, command, play.rng)
            play.commit(campaign, updated)
            return Event(input=payload, action="npc", outcome="fright decision recorded", roll=None)

        await play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
        )
