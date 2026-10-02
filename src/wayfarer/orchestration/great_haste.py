"""Private GreatHaste service: current membership, CAS and recorded entropy."""

import hashlib
import json

from wayfarer import validation
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.great_haste_host import apply_host
from wayfarer.engine.simulation.magic.great_haste_state import (
    RECEIPT,
    CastGreatHaste,
    GreatHasteReceipt,
)
from wayfarer.engine.simulation.magic.great_haste_step_state import HOST_ADAPTER as ADAPTER
from wayfarer.engine.simulation.magic.great_haste_step_state import HostCommand, StepCastGreatHaste
from wayfarer.engine.simulation.magic.spells import PROFILE
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste_combat import cast_in_combat
from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, capture
from wayfarer.orchestration.great_haste_steps import cast_with_step
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


class GreatHasteService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        state: PlayState,
        command: HostCommand,
        principal_id: str,
        *,
        combat_casting: bool = False,
    ) -> CommandPlan[GreatHasteReceipt]:
        if self.play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("GreatHaste requires the exact Basic Set profile")
        member = member_for(state, principal_id)
        controls: tuple[Control, ...]
        if isinstance(command, (CastGreatHaste, StepCastGreatHaste)):
            controls = (
                (Controls(member, command.actor_id, state=state),)
                if member.role == "player"
                else (Seats(state), Trusted(self.play.engine.reviewer.gm_ids))
            )
        else:
            controls = (
                Seats(state),
                Trusted(self.play.engine.reviewer.gm_ids),
                ActsAs(command.actor_id),
            )
        payload = json.dumps(
            {
                "operation": "great-haste",
                "generation": 2 if isinstance(command, StepCastGreatHaste) else 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        if combat_casting:
            decoded = validation.mapping(validation.decode(payload))
            decoded[KEY], decoded[ORIGINAL] = 1, payload
            payload = json.dumps(decoded, sort_keys=True)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = self.play._load(campaign)
            if isinstance(command, StepCastGreatHaste):
                if not combat_casting:
                    raise ValidationError("Selected Step requires authenticated casting generation")
                updated, result = cast_with_step(self.play, before, command)
            else:
                updated, result = (
                    cast_in_combat(self.play, before, command)
                    if combat_casting
                    and isinstance(command, (CastGreatHaste, StepCastGreatHaste))
                    and command.operation != "cancel"
                    and any(
                        e.status == "active" and command.actor_id in e.turn_order
                        for e in before.encounters
                    )
                    else apply_host(self.play.rules_context, before, command)
                )
            updated = self.play.checkpoint(updated, before=before)
            self.play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="great_haste:" + result.outcome)

        async def outcome(campaign: Campaign) -> GreatHasteReceipt:
            return GreatHasteReceipt.model_validate_json(
                next(
                    e.kind
                    for e in self.play._load(campaign).resources.events
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
            rng=self.play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> GreatHasteReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        service = self if play is self.play else GreatHasteService(play)
        combat_casting = await capture(play.store, cid, command.id)
        return await submit(
            play,
            cid,
            service.plan(
                play._load(campaign), command, principal_id, combat_casting=combat_casting
            ),
            principal_id=principal_id,
        )
