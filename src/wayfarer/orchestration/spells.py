"""Approved spell transactions on the existing campaign ledger; no frozen v1 route."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellExecutionContext as SpellExecutionContext,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    SpellResolver as SpellResolver,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    _recorded_spell_result as _recorded_spell_result,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    advance_cast_turn as advance_cast_turn,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    apparent_result as apparent_result,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    approved_context as approved_context,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    combat_guard as combat_guard,
)
from wayfarer.engine.simulation.magic.spell_transitions import (
    reduce_spell as reduce_spell,
)
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellCommand,
    SpellEvent,
    SpellResult,
    event_id,
)
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class SpellService:
    """An internal transaction seam, not permission to invent spell bindings."""

    def __init__(self, play: PlayService, resolve: SpellResolver | None = None) -> None:
        self.play, self.resolve = play, resolve

    async def execute(
        self,
        cid: str,
        value: object,
        *,
        authenticated_gm_id: str | None = None,
        principal_id: str | None = None,
    ) -> SpellResult:
        command = SpellCommand.model_validate(value)
        if command.target_item_id is not None and command.kind != "release":
            raise ValidationError("Object targeting requires a missile release")
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        if (principal_id is None) == (authenticated_gm_id is None):
            raise AuthorizationError("Select exactly one authenticated spell principal")
        identity = principal_id or authenticated_gm_id
        assert identity is not None
        member = CampaignAccess(play)._member(state, identity)
        if principal_id is not None:
            if (
                self.resolve is not None
                or member.role != "player"
                or command.actor_id not in member.actor_ids
            ):
                raise AuthorizationError("Spell actor is not controlled by principal")
        elif member.role != "gm" or identity not in play.engine.reviewer.gm_ids:
            raise ValidationError("Spell lifecycle requires trusted director authority")
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Spell lifecycle requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-lifecycle",
                "principal_id": identity,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        execution = SpellExecutionContext(play.rules_context, self.resolve)

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = reduce_spell(before, command, execution)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            result = _recorded_spell_result(updated, command)
            # Roll targets, opposed traces, and bindings stay in the private ledger.
            return CommandReceipt(
                action="resource",
                outcome="spell:" + apparent_result(updated.resources, command, result).outcome,
            )

        committed = await commit_command(
            play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            reduce,
            actor_id=identity,
            rng=play.rng,
        )
        saved = play._load(committed["state"])
        recorded = next(e for e in saved.resources.events if e.id == event_id(command.id))
        result = SpellEvent.model_validate_json(recorded.kind).result
        return (
            apparent_result(saved.resources, command, result)
            if principal_id is not None
            else result
        )
