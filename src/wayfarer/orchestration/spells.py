"""Approved spell transactions on the existing campaign ledger; no frozen v1 route."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.campaign.access import CampaignMember
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
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Trusted, submit
from wayfarer.orchestration.play import PlayService


class SpellService:
    """An internal transaction seam, not permission to invent spell bindings."""

    def __init__(self, play: PlayService, resolve: SpellResolver | None = None) -> None:
        self.play, self.resolve = play, resolve

    def plan(
        self, play: PlayService, member: CampaignMember, command: SpellCommand, *, principal_id: str
    ) -> CommandPlan[SpellResult]:
        """What a spell lifecycle command writes; the pipeline decides whether it runs.

        Two principals reach this family. A seated player casts for an actor they
        control and reads back only what the table can see; the director drives the
        lifecycle and reads the recorded result. Membership says which, so the two
        differ in their declared rules and in what they read back, not in a branch
        inside the transaction.
        """
        player = member.role == "player"
        if player and self.resolve is not None:
            raise AuthorizationError("Spell actor is not controlled by principal")
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Spell lifecycle requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "spell-lifecycle",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        execution = SpellExecutionContext(play.rules_context, self.resolve)

        def resolve(campaign: Campaign) -> CommandReceipt:
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

        async def outcome(campaign: Campaign) -> SpellResult:
            saved = play._load(campaign)
            recorded = next(e for e in saved.resources.events if e.id == event_id(command.id))
            result = SpellEvent.model_validate_json(recorded.kind).result
            if not player:
                return result
            return apparent_result(saved.resources, command, result)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(
                Controls(member, command.actor_id, "Spell actor is not controlled by principal")
                if player
                else Trusted(
                    play.engine.reviewer.gm_ids,
                    refusal="Spell lifecycle requires trusted director authority",
                ),
            ),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> SpellResult:
        command = SpellCommand.model_validate(value)
        if command.target_item_id is not None and command.kind != "release":
            raise ValidationError("Object targeting requires a missile release")
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        member = member_for(state, principal_id)
        return await submit(
            play,
            cid,
            self.plan(play, member, command, principal_id=principal_id),
            principal_id=principal_id,
        )
