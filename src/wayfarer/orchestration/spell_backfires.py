"""GM selection of campaign-authored B236 consequences, committed once."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.backfire_transitions import (
    ResolveSpellBackfire as ResolveSpellBackfire,
)
from wayfarer.engine.simulation.magic.backfire_transitions import resolve as reduce_backfire
from wayfarer.engine.simulation.magic.backfires import Backfire, backfires
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService

REFUSAL = "Backfire interpretation requires campaign GM authority"


class SpellBackfireService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        member: CampaignMember,
        command: ResolveSpellBackfire,
        *,
        principal_id: str,
    ) -> CommandPlan[Backfire]:
        """What a backfire interpretation writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {
                "operation": "spell-backfire",
                "principal": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, item = reduce_backfire(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="spell:backfire-resolved")

        async def outcome(campaign: Campaign) -> Backfire:
            saved = play._load(campaign)
            return next(b for b in backfires(saved.resources) if b.id == command.backfire_id)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(
                # The principal must be able to act as the director this command names.
                Controls(member, command.actor_id, REFUSAL),
                Seats(state, refusal=REFUSAL),
                Trusted(play.engine.reviewer.gm_ids, refusal=REFUSAL),
            ),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> Backfire:
        command = ResolveSpellBackfire.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        return await submit(
            play,
            cid,
            self.plan(
                play, state, member_for(state, principal_id), command, principal_id=principal_id
            ),
            principal_id=principal_id,
        )
