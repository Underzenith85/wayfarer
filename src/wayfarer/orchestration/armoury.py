"""Trusted Armoury familiarity observations through the campaign CAS pipeline."""

import hashlib
import json
from typing import overload

from pydantic import TypeAdapter

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.equipment.armoury_context import (
    PREFIX,
    ArmouryFamiliarity,
    DeclareArmouryFamiliarity,
    declare,
)
from wayfarer.engine.simulation.equipment.repair_parts import (
    PREFIX as PARTS_PREFIX,
)
from wayfarer.engine.simulation.equipment.repair_parts import (
    AssessRepairParts,
    RepairPartsAssessment,
)
from wayfarer.engine.simulation.equipment.repair_parts_assessment import assess
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService

ADAPTER: TypeAdapter[DeclareArmouryFamiliarity | AssessRepairParts] = TypeAdapter(
    DeclareArmouryFamiliarity | AssessRepairParts
)
ArmouryOutcome = ArmouryFamiliarity | RepairPartsAssessment


class ArmouryService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    @overload
    async def execute(
        self, cid: str, value: DeclareArmouryFamiliarity, *, principal_id: str
    ) -> ArmouryFamiliarity: ...

    @overload
    async def execute(
        self, cid: str, value: AssessRepairParts, *, principal_id: str
    ) -> RepairPartsAssessment: ...

    @overload
    async def execute(self, cid: str, value: object, *, principal_id: str) -> ArmouryOutcome: ...

    async def execute(self, cid: str, value: object, *, principal_id: str) -> ArmouryOutcome:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        initial = play._load(campaign)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, AssessRepairParts):
                updated, _ = assess(play.rules_context, before, command)
            else:
                updated, _ = declare(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="armoury:familiarity")

        async def outcome(campaign: Campaign) -> ArmouryOutcome:
            model = (
                RepairPartsAssessment
                if isinstance(command, AssessRepairParts)
                else ArmouryFamiliarity
            )
            prefix = PARTS_PREFIX if isinstance(command, AssessRepairParts) else PREFIX
            return model.model_validate_json(
                next(
                    event.kind
                    for event in play._load(campaign).resources.events
                    if event.id == prefix + hashlib.sha256(command.id.encode()).hexdigest()
                )
            )

        payload = json.dumps(
            {
                "operation": "armoury",
                "generation": 1,
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )
        plan: CommandPlan[ArmouryOutcome] = CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Controls(member_for(initial, principal_id), command.actor_id, state=initial),)
            if isinstance(command, AssessRepairParts)
            else (Seats(initial), Trusted(play.engine.reviewer.gm_ids), ActsAs(command.actor_id)),
            rng=play.rng,
        )
        return await submit(play, cid, plan, principal_id=principal_id)
