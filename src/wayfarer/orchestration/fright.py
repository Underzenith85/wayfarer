"""Director-only care and panic decisions using the existing campaign CAS ledger."""

import json

from wayfarer.engine.simulation.health.fright_transitions import FrightDecision as FrightDecision
from wayfarer.engine.simulation.health.fright_transitions import apply_decision
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


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

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated = apply_decision(before, command, play.rng)
            play.commit(campaign, updated)
            return CommandReceipt(action="npc", outcome="fright decision recorded")

        await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
            rng=play.rng,
        )
