"""Actual trusted GM producer for B173 societal default observations."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.campaign.default_knowledge import (
    ADAPTER,
    DefaultKnowledge,
    observations,
)
from wayfarer.engine.simulation.campaign.default_knowledge_admission import declare
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class DefaultKnowledgeService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, principal_id: str) -> DefaultKnowledge:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        before = play._load(campaign)

        def resolve(current: Campaign) -> CommandReceipt:
            updated, _ = declare(play.rules_context, play._load(current), command)
            play.commit(current, updated)
            return CommandReceipt(action="resource", outcome="default-knowledge:declared")

        async def outcome(current: Campaign) -> DefaultKnowledge:
            return next(
                observation
                for observation in observations(play._load(current).resources)
                if observation.command_id == command.id
            )

        payload = json.dumps(
            {
                "operation": "default-knowledge",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        plan = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            actor_id=principal_id,
            resolve=resolve,
            outcome=outcome,
            control=(Seats(before), Trusted(play.engine.reviewer.gm_ids), ActsAs(command.actor_id)),
            rng=play.rng,
        )
        return await submit(play, cid, plan, principal_id=principal_id)
