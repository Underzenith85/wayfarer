"""Private water transactions on the canonical revision/receipt pipeline."""

import json
from typing import Annotated

from pydantic import Field, TypeAdapter

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.spell_state import SpellResult, event_id, parse_event
from wayfarer.engine.simulation.magic.spell_transitions import SpellExecutionContext, reduce_spell
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand
from wayfarer.engine.simulation.magic.water_discovery import WaterSpellResult, command_finding
from wayfarer.engine.simulation.magic.water_host import (
    DeclareWater,
    DeclareWaterChannel,
    WaterHostCommand,
    WaterReceipt,
    apply_host,
    receipt_id,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Control, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService

WaterCommand = Annotated[
    DeclareWater | DeclareWaterChannel | RuntimeSpellCommand, Field(discriminator="kind")
]
ADAPTER: TypeAdapter[WaterCommand] = TypeAdapter(WaterCommand)

WATER_SPELLS = frozenset({"seek-water", "purify-water", "create-water", "destroy-water"})


class WaterService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: WaterHostCommand | RuntimeSpellCommand,
        *,
        principal_id: str,
    ) -> CommandPlan[WaterReceipt | SpellResult]:
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Water requires the exact Basic Set profile")
        if isinstance(command, RuntimeSpellCommand) and command.spell_id not in WATER_SPELLS:
            raise ValidationError("Water host only executes its four admitted named spells")
        member = member_for(state, principal_id)
        controls: tuple[Control, ...]
        if isinstance(command, RuntimeSpellCommand) and member.role == "player":
            controls = (Controls(member, command.actor_id),)
        else:
            controls = (Seats(state), Trusted(play.engine.reviewer.gm_ids))
        payload = json.dumps(
            {
                "operation": "water",
                "generation": 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            result: WaterReceipt | SpellResult
            if isinstance(command, RuntimeSpellCommand):
                updated, result = reduce_spell(
                    before, command, SpellExecutionContext(play.rules_context)
                )
            else:
                updated, result = apply_host(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="water:" + result.outcome)

        async def outcome(campaign: Campaign) -> WaterReceipt | SpellResult:
            resources = play._load(campaign).resources
            if isinstance(command, RuntimeSpellCommand):
                result = parse_event(
                    next(
                        e
                        for e in resources.events
                        if e.id == event_id(command.id, command.spell_id)
                    )
                ).result
                discovered = command_finding(resources, command.id, command.actor_id)
                return (
                    WaterSpellResult.model_validate_json(
                        json.dumps(
                            {
                                **result.model_dump(mode="json"),
                                "finding": discovered.model_dump(mode="json"),
                            }
                        )
                    )
                    if discovered is not None
                    else result
                )
            return WaterReceipt.model_validate_json(
                next(e.kind for e in resources.events if e.id == receipt_id(command.id))
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=controls,
            rng=play.rng,
        )

    async def execute(
        self, cid: str, value: object, *, principal_id: str
    ) -> WaterReceipt | SpellResult:
        # No public API adapter is widened by this private host.
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
