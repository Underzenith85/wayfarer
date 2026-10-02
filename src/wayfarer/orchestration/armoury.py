"""Trusted Armoury familiarity observations through the campaign CAS pipeline."""

import hashlib
import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.equipment.armoury_context import (
    ADAPTER,
    PREFIX,
    ArmouryFamiliarity,
    declare,
)
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class ArmouryService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, principal_id: str) -> ArmouryFamiliarity:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        initial = play._load(campaign)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, _ = declare(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="armoury:familiarity")

        async def outcome(campaign: Campaign) -> ArmouryFamiliarity:
            return ArmouryFamiliarity.model_validate_json(
                next(
                    event.kind
                    for event in play._load(campaign).resources.events
                    if event.id == PREFIX + hashlib.sha256(command.id.encode()).hexdigest()
                )
            )

        payload = json.dumps(
            {
                "operation": "armoury",
                "generation": 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        plan: CommandPlan[ArmouryFamiliarity] = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(
                Seats(initial),
                Trusted(play.engine.reviewer.gm_ids),
                ActsAs(command.actor_id),
            ),
            rng=play.rng,
        )
        return await submit(play, cid, plan, principal_id=principal_id)
