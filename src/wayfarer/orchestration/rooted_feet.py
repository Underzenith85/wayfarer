"""Authenticated private Rooted Feet transactions on the canonical ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.rooted_feet_policy import RootedGeneration, rooted_generation
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    ADAPTER,
    ObserveRootedFeetSubject,
    RootedFeetCommand,
    RootedFeetReceipt,
    identifier,
)
from wayfarer.engine.simulation.magic.rooted_feet_transitions import apply
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.rooted_feet_generations import capture


class RootedFeetService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: RootedFeetCommand,
        *,
        principal_id: str,
        generation: RootedGeneration,
    ) -> CommandPlan[RootedFeetReceipt]:
        trusted = isinstance(command, ObserveRootedFeetSubject)
        payload = json.dumps(
            {
                "operation": "rooted-feet",
                "generation": generation,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            with rooted_generation(generation):
                updated, result = apply(play.rules_context, before, command)
            revision = before.revision + 1
            updated = updated.model_copy(
                update={
                    "revision": revision,
                    "resources": updated.resources.model_copy(update={"revision": revision}),
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="rooted-feet:" + result.outcome)

        async def outcome(campaign: Campaign) -> RootedFeetReceipt:
            event = next(
                e
                for e in play._load(campaign).resources.events
                if e.id == identifier("receipt", command.id)
            )
            return RootedFeetReceipt.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids))
            if trusted
            else (Controls(member_for(state, principal_id), command.actor_id, state=state),),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> RootedFeetReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        generation = await capture(play.store, cid, command.id)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                play._load(campaign),
                command,
                principal_id=principal_id,
                generation=generation,
            ),
            principal_id=principal_id,
        )
