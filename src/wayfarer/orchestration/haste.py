"""Private Haste transactions through the canonical command pipeline."""

import hashlib
import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.haste_host import ADAPTER, HasteCommand, apply_host
from wayfarer.engine.simulation.magic.haste_state import (
    RECEIPT,
    HasteReceipt,
    SwitchHasteItem,
    channels,
    items,
)
from wayfarer.engine.simulation.magic.item_receipt_privacy import item_result
from wayfarer.engine.simulation.magic.spell_state import SpellResult, event_id, parse_event
from wayfarer.engine.simulation.magic.spell_transitions import SpellExecutionContext, reduce_spell
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import (
    ActsAs,
    CommandPlan,
    Control,
    Controls,
    Seats,
    Trusted,
    submit,
)
from wayfarer.orchestration.play import PlayService


class HasteService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: HasteCommand, *, principal_id: str
    ) -> CommandPlan[SpellResult | HasteReceipt]:
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Haste requires the exact Basic Set profile")
        if isinstance(command, RuntimeSpellCommand) and command.spell_id != "haste":
            raise ValidationError("Haste host only executes Haste")
        member = member_for(state, principal_id)
        if isinstance(command, (RuntimeSpellCommand, SwitchHasteItem)):
            controls: tuple[Control, ...] = (
                (Controls(member, command.actor_id),)
                if member.role == "player"
                else (Seats(state), Trusted(play.engine.reviewer.gm_ids))
            )
        else:
            controls = (
                Seats(state),
                Trusted(play.engine.reviewer.gm_ids),
                ActsAs(command.actor_id),
            )
        payload = json.dumps(
            {
                "operation": "haste",
                "generation": 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            result: SpellResult | HasteReceipt
            if isinstance(command, RuntimeSpellCommand):
                updated, result = reduce_spell(
                    before, command, SpellExecutionContext(play.rules_context)
                )
            else:
                updated, result = apply_host(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="haste:" + result.outcome)

        async def outcome(campaign: Campaign) -> SpellResult | HasteReceipt:
            resources = play._load(campaign).resources
            if isinstance(command, RuntimeSpellCommand):
                result = parse_event(
                    next(e for e in resources.events if e.id == event_id(command.id, "haste"))
                ).result
                if member.role != "player" or not result.checks:
                    return result
                original = play._load(
                    await play.store.replay(campaign["id"], command.expected_revision)
                )
                channel = next(
                    (c for c in channels(original.resources) if c.id == command.channel_id), None
                )
                item_id = channel.magic_item_id if channel else None
                metadata = next(
                    (v for v in reversed(items(original.resources)) if v.item_id == item_id), None
                )
                return item_result(
                    original.resources,
                    result,
                    item_id=item_id,
                    spell_id=command.spell_id,
                    binding_id=metadata.binding_id if metadata else None,
                )
            return HasteReceipt.model_validate_json(
                next(
                    e.kind
                    for e in resources.events
                    if e.id == RECEIPT + hashlib.sha256(command.id.encode()).hexdigest()
                )
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=controls,
            rng=play.rng,
        )

    async def execute(
        self, cid: str, value: object, *, principal_id: str
    ) -> SpellResult | HasteReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
