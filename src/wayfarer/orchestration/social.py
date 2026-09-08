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


def dispatch(
    play: PlayService,
    before: PlayState,
    command: SocialCommand,
    interaction: ResolvedInteraction,
) -> tuple[PlayState, SocialOutcome]:
    """Trusted reducer shared by director commands and authored NPC occurrences."""
    profile_id = play.engine.reviewer.compiler.statistics_profile
    if interaction.context.profile_id != profile_id:
        raise ValidationError("Social context does not match campaign profile")
    fright_modifier = 0
    if command.kind == "fright":
        from wayfarer.simulation.fright import validate_subject

        validate_subject(before.resources, command.subject_id, profile_id)
        from wayfarer.orchestration.gurps_melee import build

        if not any(a.actor_id == command.subject_id for a in before.actors):
            raise ValidationError("Fright requires an approved character")
        compiled = build(play, before, command.subject_id)
        statistics = compiled.statistics
        assert statistics is not None
        if interaction.context.ht != statistics.ht or interaction.context.will != statistics.will:
            raise ValidationError("Fright context must match approved HT and Will")
        # B43 Combat Reflexes: +2 on Fright Checks, read from the approved build
        # rather than the caller's context, which supplies only the situation.
        fright_modifier = 2 * any(
            p.definition_id == "trait:combat-reflexes" for p in compiled.purchases
        )
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
        fright_modifier=fright_modifier,
    )
    if command.kind == "fright":
        from wayfarer.rules.fright import FrightEffect
        from wayfarer.simulation.fright import apply_effect

        raw = json.loads(resources.events[-1].kind)["private"]["effect"]
        if raw is not None:
            resources = apply_effect(
                resources,
                FrightEffect.model_validate_json(json.dumps(raw)),
                actor_id=command.subject_id,
                trigger_id=command.trigger_id,
                command_id=command.id,
                ht=interaction.context.ht,
                will=interaction.context.will,
                modified_will=interaction.context.target,
                rng=play.rng,
            )
            resources = resources.model_copy(update={"revision": before.revision + 1})
    updated = before.model_copy(
        update={"revision": resources.revision, "resources": resources, "world": world}
    )
    return updated, outcome


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
            updated, outcome = dispatch(play, before, command, self.resolve(play, before, command))
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
