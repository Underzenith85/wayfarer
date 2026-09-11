"""Scenario-bound physical procedures in the existing play transaction."""

from __future__ import annotations

import json

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.mechanics.physical import PhysicalCommand as PhysicalCommand
from wayfarer.simulation.mechanics.physical import PhysicalContext as PhysicalContext
from wayfarer.simulation.mechanics.physical import PhysicalResult as PhysicalResult
from wayfarer.simulation.mechanics.physical import PhysicalRoute as PhysicalRoute
from wayfarer.simulation.mechanics.physical import RouteResolver as RouteResolver
from wayfarer.simulation.mechanics.physical import reduce_physical as reduce_physical


class PhysicalService:
    def __init__(self, play: PlayService, resolver: RouteResolver) -> None:
        self.play, self.resolver = play, resolver

    async def execute(
        self, cid: str, command: PhysicalCommand, *, authenticated_actor_id: str
    ) -> PhysicalResult:
        command = PhysicalCommand.model_validate(command)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Physical actor does not match authenticated actor")
        play = self.play.for_campaign(await self.play.store.read(cid))
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Physical feats require the exact Basic Set profile")
        payload = json.dumps(
            {"operation": "gurps-physical", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        context = PhysicalContext(play.rules_context, self.resolver, self.play.rng)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = reduce_physical(before, command, context)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="noncombat", outcome=result.model_dump_json())

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=play.rng,
        )
        state = play._load(committed["state"])
        event = next(e for e in state.resources.events if e.id == "feat:" + command.id)
        return PhysicalResult.model_validate_json(event.kind)
