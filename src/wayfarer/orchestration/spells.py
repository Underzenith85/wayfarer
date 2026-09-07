"""Private director spell lifecycle transactions; no frozen v1 route.

The world resolver supplies environment facts; the compiler supplies the approved build.
Play dispatch stays gated until catalog and concrete effect consumers land.
"""

import json
from collections.abc import Callable

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import guard
from wayfarer.orchestration.spell_bindings import SpellEnvironment, approved_context
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.spells import (
    PROFILE,
    SpellCommand,
    SpellEvent,
    SpellResult,
    apply_spell,
    event_id,
)

SpellResolver = Callable[[PlayService, PlayState, SpellCommand], SpellEnvironment]


class SpellService:
    """An internal transaction seam, not permission to invent spell bindings."""

    def __init__(self, play: PlayService, resolve: SpellResolver) -> None:
        self.play, self.resolve = play, resolve

    async def execute(self, cid: str, value: object, *, authenticated_gm_id: str) -> SpellResult:
        command = SpellCommand.model_validate(value)
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = CampaignAccess(play)._member(state, authenticated_gm_id)
        if member.role != "gm" or authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("Spell lifecycle requires trusted director authority")
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Spell lifecycle requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-lifecycle",
                "principal_id": authenticated_gm_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> Event:
            before = play._load(campaign)
            guard(before, command.actor_id, "spell")
            if any(
                e.status == "active" and command.actor_id in e.turn_order for e in before.encounters
            ):
                raise ValidationError("Combat spell dispatch requires the maneuver adapter")
            environment = SpellEnvironment.model_validate(self.resolve(play, before, command))
            context = approved_context(play, before, command, environment)
            if context.profile_id != PROFILE:
                raise ValidationError("Spell context does not match campaign profile")
            if command.actor_id not in {a.actor_id for a in before.actors}:
                raise ValidationError("Unknown caster")
            perceived = {e.id for e in before.world.perspective(command.actor_id).entities}
            if context.target_id != command.actor_id and context.target_id not in perceived:
                raise ValidationError("Spell target is not perceived")
            if command.kind == "start":
                from wayfarer.simulation.concentration import require_idle_concentration

                require_idle_concentration(before.resources, command.actor_id)
            resources, result = apply_spell(
                before.resources, command, context, rng=play.rng, system=True
            )
            updated = before.model_copy(
                update={"revision": resources.revision, "resources": resources}
            )
            updated = play.checkpoint(updated, before=before)
            play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            # Roll targets, opposed traces, and bindings stay in the private ledger.
            return Event(
                input=payload, action="resource", outcome="spell:" + result.outcome, roll=None
            )

        committed = await play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=authenticated_gm_id,
        )
        saved = play._load(committed["state"])
        recorded = next(e for e in saved.resources.events if e.id == event_id(command.id))
        return SpellEvent.model_validate_json(recorded.kind).result
