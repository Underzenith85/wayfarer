"""Scenario-bound physical procedures in the existing play transaction."""

from __future__ import annotations

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.movement.physical import PhysicalCommand as PhysicalCommand
from wayfarer.engine.simulation.movement.physical import PhysicalContext as PhysicalContext
from wayfarer.engine.simulation.movement.physical import PhysicalResult as PhysicalResult
from wayfarer.engine.simulation.movement.physical import PhysicalRoute as PhysicalRoute
from wayfarer.engine.simulation.movement.physical import RouteResolver as RouteResolver
from wayfarer.engine.simulation.movement.physical import reduce_physical as reduce_physical
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService


class PhysicalService:
    def __init__(self, play: PlayService, resolver: RouteResolver) -> None:
        self.play, self.resolver = play, resolver

    def plan(
        self, play: PlayService, command: PhysicalCommand, *, principal_id: str
    ) -> CommandPlan[PhysicalResult]:
        """What a physical feat writes; the pipeline decides whether it runs."""
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

        async def outcome(campaign: Campaign) -> PhysicalResult:
            state = play._load(campaign)
            event = next(e for e in state.resources.events if e.id == "feat:" + command.id)
            return PhysicalResult.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(
                ActsAs(command.actor_id, "Physical actor does not match authenticated actor"),
            ),
            rng=play.rng,
        )

    async def execute(
        self, cid: str, command: PhysicalCommand, *, principal_id: str
    ) -> PhysicalResult:
        command = PhysicalCommand.model_validate(command)
        play = self.play.for_campaign(await self.play.store.read(cid))
        return await submit(
            play,
            cid,
            self.plan(play, command, principal_id=principal_id),
            principal_id=principal_id,
        )
