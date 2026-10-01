"""Private Staff intentions and current-director contact on the shared CAS ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.spells import PROFILE
from wayfarer.engine.simulation.magic.staff_casting import apply_command
from wayfarer.engine.simulation.magic.staff_casting_state import (
    ADAPTER,
    INTENT_PREFIX,
    TOUCH_PREFIX,
    DeclareStaffIntent,
    ObserveStaffTouch,
    StaffIntent,
    StaffTouch,
    identifier,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class StaffCastingService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: DeclareStaffIntent | ObserveStaffTouch,
        *,
        principal_id: str,
    ) -> CommandPlan[StaffIntent | StaffTouch]:
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Staff casting requires the exact Basic Set profile")
        member = member_for(state, principal_id)
        trusted = (Seats(state), Trusted(play.engine.reviewer.gm_ids))
        control = (
            trusted
            if isinstance(command, ObserveStaffTouch) or member.role != "player"
            else (Controls(member, command.actor_id, state=state),)
        )
        payload = json.dumps(
            {
                "operation": "staff-casting",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, _ = apply_command(
                play.rules_context, before, command, principal_id=principal_id
            )
            play.commit(campaign, play.checkpoint(updated, before=before))
            return CommandReceipt(action="resource", outcome="staff-casting:" + command.kind)

        async def outcome(campaign: Campaign) -> StaffIntent | StaffTouch:
            prefix = INTENT_PREFIX if isinstance(command, DeclareStaffIntent) else TOUCH_PREFIX
            encoded = next(
                e.kind
                for e in play._load(campaign).resources.events
                if e.id == identifier(prefix, command.id)
            )
            return (
                StaffIntent.model_validate_json(encoded)
                if isinstance(command, DeclareStaffIntent)
                else StaffTouch.model_validate_json(encoded)
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=control,
            rng=play.rng,
        )

    async def execute(
        self, cid: str, value: object, *, principal_id: str
    ) -> StaffIntent | StaffTouch:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
