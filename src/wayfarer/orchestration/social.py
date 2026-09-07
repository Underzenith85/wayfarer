"""Internal, transactional social dispatch; no player-supplied rules context."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    SocialOutcome,
    apply_interaction,
    apply_social,
)


@dataclass(frozen=True)
class ResolvedInteraction:
    context: SocialContext
    disclosure: SocialDisclosure = SocialDisclosure()


InteractionResolver = Callable[[PlayService, PlayState, SocialCommand], ResolvedInteraction]


class SocialService:
    """Bind a trusted scenario/NPC trigger resolver, then commit through PlayService.

    This adapter does not enable an unverified profile in the profile registry.
    The resolver must reject unrecognized triggers and derive effective values
    from approved character builds and bounded scenario definitions.
    """

    def __init__(self, play: PlayService, resolve: InteractionResolver) -> None:
        self.play, self.resolve = play, resolve

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> SocialOutcome:
        try:
            command = SocialCommand.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid social command") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = CampaignAccess(play)._member(state, authenticated_gm_id)
        if member.role != "gm" or authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Social dispatch requires trusted director authority")
        profile_id = play.engine.reviewer.compiler.statistics_profile
        if profile_id is None:
            raise ValidationError("Social dispatch requires an exact GURPS profile")
        payload = json.dumps(
            {
                "operation": "gurps-social",
                "principal_id": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> Event:
            before = play._load(campaign)
            if command.kind == "fright":
                raise ValidationError("Fright dispatch requires timed consequence integration")
            interaction = self.resolve(play, before, command)
            if interaction.context.profile_id != profile_id:
                raise ValidationError("Social context does not match campaign profile")
            # A player subject may resist fear or a disadvantage, but reaction
            # and influence never select behavior or disclose facts on their behalf.
            if command.kind in ("reaction", "influence") and any(
                m.role == "player" and command.subject_id in m.actor_ids for m in before.members
            ):
                raise ValidationError("NPC social outcomes cannot control a player character")
            resources, world, outcome = apply_interaction(
                before.resources,
                before.world,
                command,
                interaction.context,
                interaction.disclosure,
                rng=play.rng,
                system=True,
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources, "world": world}
            )
            updated = play.checkpoint(updated, before=before)
            play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            # The event stream carries neither trusted modifiers nor fact IDs.
            return Event(input=payload, action="npc", outcome=outcome.model_dump_json(), roll=None)

        result = await play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
        )
        committed = play._load(result["state"])
        # Receipt replay must not invoke the resolver again or re-evaluate facts.
        _, outcome = apply_social(
            committed.resources,
            committed.world,
            command,
            SocialContext(profile_id, 0),
            rng=play.rng,
            system=True,
        )
        return outcome
