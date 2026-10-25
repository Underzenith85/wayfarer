"""Scenario-bound physical procedures in the existing play transaction."""

from __future__ import annotations

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.movement.physical import (
    ENCHANTING_REST_KINDS,
    PHYSICAL_SKILL_KINDS,
    feat_event_id,
    require_default_route_actor,
    require_route_actor,
)
from wayfarer.engine.simulation.movement.physical import PhysicalCommand as PhysicalCommand
from wayfarer.engine.simulation.movement.physical import PhysicalContext as PhysicalContext
from wayfarer.engine.simulation.movement.physical import PhysicalResult as PhysicalResult
from wayfarer.engine.simulation.movement.physical import PhysicalRoute as PhysicalRoute
from wayfarer.engine.simulation.movement.physical import RouteResolver as RouteResolver
from wayfarer.engine.simulation.movement.physical import reduce_physical as reduce_physical
from wayfarer.errors import ValidationError
from wayfarer.orchestration.ability_boundary import require_boundary
from wayfarer.orchestration.physical_generations import admission, decode, encode
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService


class PhysicalService:
    def __init__(self, play: PlayService, resolver: RouteResolver) -> None:
        self.play, self.resolver = play, resolver

    def plan(
        self,
        play: PlayService,
        command: PhysicalCommand,
        *,
        principal_id: str,
        route: PhysicalRoute | None = None,
        enchanting_rest: bool = True,
        skill_minimum: bool = False,
    ) -> CommandPlan[PhysicalResult]:
        """What a physical feat writes; the pipeline decides whether it runs."""
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Physical feats require the exact Basic Set profile")
        enchanting_rest = enchanting_rest and command.kind in ENCHANTING_REST_KINDS
        if (skill_minimum and route is None) or (
            route is not None and not (enchanting_rest or skill_minimum)
        ):
            raise ValidationError("Physical admission requires its captured route and generation")
        payload = json.dumps(
            {
                "operation": "gurps-physical",
                "command": command.model_dump(mode="json"),
                **(
                    {
                        "physical_route": encode(route),
                        **({"enchanting_rest_generation": 1} if enchanting_rest else {}),
                        **({"physical_skill_minimum_generation": 1} if skill_minimum else {}),
                    }
                    if route is not None
                    else {}
                ),
            },
            sort_keys=True,
        )

        context = PhysicalContext(
            play.rules_context,
            self.resolver if route is None else lambda *_: route,
            self.play.rng,
            enchanting_rest=route is not None and enchanting_rest,
            skill_minimum=skill_minimum,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = reduce_physical(before, command, context)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="noncombat", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> PhysicalResult:
            state = play._load(campaign)
            event = next(e for e in state.resources.events if e.id == feat_event_id(command.id))
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
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        route = None
        rest, minimum = False, False
        if command.kind in ENCHANTING_REST_KINDS | PHYSICAL_SKILL_KINDS:
            capture = await admission(play, cid, command.id)
            rest = capture.enchanting_rest and command.kind in ENCHANTING_REST_KINDS
            minimum = capture.skill_minimum and command.kind in PHYSICAL_SKILL_KINDS
            if rest or minimum:
                route = capture.route
                if route is None:
                    if await play.store.command_input(cid, command.id) is None:
                        require_boundary(campaign, None)
                    state = play._load(campaign)
                    require_route_actor(state, command, enchanting_rest=rest)
                    require_default_route_actor(play.rules_context, state, command)
                    route = decode(
                        encode(
                            self.resolver(
                                play.rules_context,
                                state,
                                command.actor_id,
                                command.route_id,
                            )
                        )
                    )
        return await submit(
            play,
            cid,
            self.plan(
                play,
                command,
                principal_id=principal_id,
                route=route,
                enchanting_rest=rest,
                skill_minimum=minimum,
            ),
            principal_id=principal_id,
        )
