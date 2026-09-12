"""B393 surprise at the start of combat, persisted through the combat ledger."""

import json

from wayfarer.engine.simulation.mechanics.surprise import Resolver as Resolver
from wayfarer.engine.simulation.mechanics.surprise import SurpriseCommand as SurpriseCommand
from wayfarer.engine.simulation.mechanics.surprise import SurpriseSides as SurpriseSides
from wayfarer.engine.simulation.mechanics.surprise import apply_surprise as apply_surprise
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class SurpriseService:
    def __init__(self, play: PlayService, resolve: Resolver) -> None:
        self.play, self.resolve = play, resolve

    async def execute(self, cid: str, command: SurpriseCommand, *, gm_id: str) -> None:
        command = SurpriseCommand.model_validate(command)
        if not command.trigger_id:
            raise ValidationError("Surprise requires a stable authored trigger")
        play = self.play.for_campaign(await self.play.store.read(cid))
        payload = json.dumps(
            {"surprise": command.model_dump(mode="json"), "gm": gm_id}, sort_keys=True
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            state = play._load(campaign)
            updated = apply_surprise(state, command, play.rules_context, self.resolve, gm_id)
            updated = play.checkpoint(updated, before=state)
            play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome="resolved")

        await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=gm_id,
            rng=play.rng,
        )
