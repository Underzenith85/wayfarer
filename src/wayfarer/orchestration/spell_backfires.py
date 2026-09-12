"""GM selection of campaign-authored B236 consequences, committed once."""

import json

from wayfarer.engine.simulation.mechanics.spell_backfires import (
    ResolveSpellBackfire as ResolveSpellBackfire,
)
from wayfarer.engine.simulation.mechanics.spell_backfires import resolve
from wayfarer.engine.simulation.spell_backfires import Backfire, backfires
from wayfarer.errors import AuthorizationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class SpellBackfireService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> Backfire:
        command = ResolveSpellBackfire.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        initial = play._load(await play.store.read(cid))
        member = CampaignAccess(play)._member(initial, authenticated_gm_id)
        if (
            command.actor_id != authenticated_gm_id
            or member.role != "gm"
            or authenticated_gm_id not in play.engine.reviewer.gm_ids
        ):
            raise AuthorizationError("Backfire interpretation requires campaign GM authority")
        payload = json.dumps(
            {
                "operation": "spell-backfire",
                "principal": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, item = resolve(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="spell:backfire-resolved")

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
            rng=play.rng,
        )
        state = play._load(committed["state"])
        return next(b for b in backfires(state.resources) if b.id == command.backfire_id)
