"""Authenticated private personal Wither Limb transactions on the canonical ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.wither_spell_commands import ADAPTER, CastWitherLimb
from wayfarer.engine.simulation.magic.wither_spell_state import (
    WitherLimbReceipt,
    identifier,
)
from wayfarer.engine.simulation.magic.wither_spell_transitions import apply
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, submit
from wayfarer.orchestration.play import PlayService


class WitherSpellService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: CastWitherLimb, *, principal_id: str
    ) -> CommandPlan[WitherLimbReceipt]:
        payload = json.dumps(
            {
                "operation": "wither-spell",
                "generation": 3,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = apply(play.rules_context, before, command)
            revision = before.revision + 1
            updated = updated.model_copy(
                update={
                    "revision": revision,
                    "resources": updated.resources.model_copy(update={"revision": revision}),
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="wither-spell:" + result.outcome)

        async def outcome(campaign: Campaign) -> WitherLimbReceipt:
            event = next(
                e
                for e in play._load(campaign).resources.events
                if e.id == identifier("receipt", command.id)
            )
            return WitherLimbReceipt.model_validate_json(event.kind)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Controls(member_for(state, principal_id), command.actor_id, state=state),),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> WitherLimbReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
