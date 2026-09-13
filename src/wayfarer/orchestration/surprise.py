"""B393 surprise at the start of combat, persisted through the combat ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.combat.surprise import Resolver as Resolver
from wayfarer.engine.simulation.combat.surprise import SurpriseCommand as SurpriseCommand
from wayfarer.engine.simulation.combat.surprise import SurpriseSides as SurpriseSides
from wayfarer.engine.simulation.combat.surprise import apply_surprise as apply_surprise
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit
from wayfarer.orchestration.play import PlayService


class SurpriseService:
    def __init__(self, play: PlayService, resolve: Resolver) -> None:
        self.play, self.resolve = play, resolve

    def plan(
        self, play: PlayService, command: SurpriseCommand, *, principal_id: str
    ) -> CommandPlan[None]:
        """What a surprise resolution writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {"surprise": command.model_dump(mode="json"), "gm": principal_id}, sort_keys=True
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            updated = apply_surprise(state, command, play.rules_context, self.resolve, principal_id)
            updated = play.checkpoint(updated, before=state)
            play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome="resolved")

        async def outcome(campaign: Campaign) -> None:
            return None

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(ActsAs(principal_id),),
            rng=play.rng,
        )

    async def execute(self, cid: str, command: SurpriseCommand, *, principal_id: str) -> None:
        command = SurpriseCommand.model_validate(command)
        if not command.trigger_id:
            raise ValidationError("Surprise requires a stable authored trigger")
        play = self.play.for_campaign(await self.play.store.read(cid))
        await submit(
            play,
            cid,
            self.plan(play, command, principal_id=principal_id),
            principal_id=principal_id,
        )
