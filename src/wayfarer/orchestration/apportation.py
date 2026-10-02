"""Private Apportation service: current membership, CAS and recorded entropy."""

import hashlib
import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.apportation_host import apply_host
from wayfarer.engine.simulation.magic.apportation_state import (
    ADAPTER,
    RECEIPT,
    ApportationCommand,
    ApportationReceipt,
    CastApportation,
    MoveApportation,
)
from wayfarer.engine.simulation.magic.spells import PROFILE
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


class ApportationService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, state: PlayState, command: ApportationCommand, principal_id: str
    ) -> CommandPlan[ApportationReceipt]:
        if self.play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Apportation requires the exact Basic Set profile")
        member = member_for(state, principal_id)
        controls: tuple[Control, ...]
        if isinstance(command, (CastApportation, MoveApportation)):
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
                "operation": "apportation",
                "generation": 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = self.play._load(campaign)
            updated, result = apply_host(self.play.rules_context, before, command)
            updated = self.play.checkpoint(updated, before=before)
            self.play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="apportation:" + result.outcome)

        async def outcome(campaign: Campaign) -> ApportationReceipt:
            return ApportationReceipt.model_validate_json(
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

    async def execute(self, cid: str, value: object, *, principal_id: str) -> ApportationReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        service = self if play is self.play else ApportationService(play)
        return await submit(
            play,
            cid,
            service.plan(play._load(campaign), command, principal_id),
            principal_id=principal_id,
        )
