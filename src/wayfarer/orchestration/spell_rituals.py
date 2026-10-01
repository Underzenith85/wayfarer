"""Current trusted GM observations of ordinary spell ritual capabilities."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.ritual_state import (
    DeclareRitualCapability,
    RitualCapability,
    identifier,
    save,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class SpellRitualService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: DeclareRitualCapability,
        *,
        principal_id: str,
    ) -> CommandPlan[RitualCapability]:
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Ritual capabilities require the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-ritual",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            compiled = play.rules_context.approved_build(before, command.actor_id)
            value = RitualCapability(command=command, build_revision=compiled.revision)
            resources = save(before.resources, value).model_copy(
                update={"revision": before.revision + 1}
            )
            updated = before.model_copy(
                update={"revision": before.revision + 1, "resources": resources}
            )
            play.commit(campaign, play.checkpoint(updated, before=before))
            return CommandReceipt(action="resource", outcome="spell:ritual-capability")

        async def outcome(campaign: Campaign) -> RitualCapability:
            return RitualCapability.model_validate_json(
                next(
                    e.kind
                    for e in play._load(campaign).resources.events
                    if e.id == identifier(command.id)
                )
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            actor_id=principal_id,
            resolve=resolve,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids)),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> RitualCapability:
        command = DeclareRitualCapability.model_validate(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
