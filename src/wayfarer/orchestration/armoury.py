"""Private Armoury observations, parts assessments and selected repair work."""

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
from wayfarer.engine.simulation.equipment.repair_default_admission import (
    declare_training,
    select_default,
)
from wayfarer.engine.simulation.equipment.repair_defaults import (
    DEFAULT_PREFIX,
    TRAINING_PREFIX,
    ArmouryTraining,
    DeclareArmouryTraining,
    RepairDefaultSelection,
    SelectRepairDefault,
)
from wayfarer.engine.simulation.equipment.repair_parts import (
    PREFIX as PARTS_PREFIX,
)
from wayfarer.engine.simulation.equipment.repair_parts import (
    AssessRepairParts,
    RepairPartsAssessment,
)
from wayfarer.engine.simulation.equipment.repair_parts_assessment import assess
from wayfarer.engine.simulation.equipment.repair_time import (
    PREFIX as TIME_PREFIX,
)
from wayfarer.engine.simulation.equipment.repair_time import (
    RepairTimeSelection,
    SelectRepairTime,
)
from wayfarer.engine.simulation.equipment.repair_time_selection import select
from wayfarer.engine.simulation.equipment.tool_context import (
    OBSERVATION_PREFIX,
    SELECTION_PREFIX,
    DeclareRepairTools,
    RepairToolObservation,
    RepairToolSelection,
    SelectRepairTools,
)
from wayfarer.engine.simulation.equipment.tool_context import (
    declare as declare_tools,
)
from wayfarer.engine.simulation.equipment.tool_selection import select as select_tools
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService

ArmouryCommand = (
    DeclareArmouryFamiliarity
    | AssessRepairParts
    | SelectRepairTime
    | DeclareArmouryTraining
    | SelectRepairDefault
    | DeclareRepairTools
    | SelectRepairTools
)
ADAPTER: TypeAdapter[ArmouryCommand] = TypeAdapter(ArmouryCommand)
ArmouryOutcome = (
    ArmouryFamiliarity
    | RepairPartsAssessment
    | RepairTimeSelection
    | ArmouryTraining
    | RepairDefaultSelection
    | RepairToolObservation
    | RepairToolSelection
)


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
    async def execute(
        self, cid: str, value: SelectRepairTime, *, principal_id: str
    ) -> RepairTimeSelection: ...

    @overload
    async def execute(
        self, cid: str, value: DeclareArmouryTraining, *, principal_id: str
    ) -> ArmouryTraining: ...

    @overload
    async def execute(
        self, cid: str, value: SelectRepairDefault, *, principal_id: str
    ) -> RepairDefaultSelection: ...

    @overload
    async def execute(
        self, cid: str, value: DeclareRepairTools, *, principal_id: str
    ) -> RepairToolObservation: ...

    @overload
    async def execute(
        self, cid: str, value: SelectRepairTools, *, principal_id: str
    ) -> RepairToolSelection: ...

    @overload
    async def execute(self, cid: str, value: object, *, principal_id: str) -> ArmouryOutcome: ...

    async def execute(self, cid: str, value: object, *, principal_id: str) -> ArmouryOutcome:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        initial = play._load(campaign)

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, DeclareRepairTools):
                updated, _ = declare_tools(play.rules_context, before, command)
            elif isinstance(command, SelectRepairTools):
                updated, _ = select_tools(play.rules_context, before, command)
            elif isinstance(command, DeclareArmouryTraining):
                updated, _ = declare_training(play.rules_context, before, command)
            elif isinstance(command, SelectRepairDefault):
                updated, _ = select_default(play.rules_context, before, command)
            elif isinstance(command, SelectRepairTime):
                updated, _ = select(play.rules_context, before, command)
            elif isinstance(command, AssessRepairParts):
                updated, _ = assess(play.rules_context, before, command)
            else:
                updated, _ = declare(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(
                action="resource",
                outcome="armoury:time"
                if isinstance(command, SelectRepairTime)
                else "armoury:familiarity",
            )

        async def outcome(campaign: Campaign) -> ArmouryOutcome:
            model = (
                RepairToolObservation
                if isinstance(command, DeclareRepairTools)
                else RepairToolSelection
                if isinstance(command, SelectRepairTools)
                else ArmouryTraining
                if isinstance(command, DeclareArmouryTraining)
                else RepairDefaultSelection
                if isinstance(command, SelectRepairDefault)
                else RepairTimeSelection
                if isinstance(command, SelectRepairTime)
                else RepairPartsAssessment
                if isinstance(command, AssessRepairParts)
                else ArmouryFamiliarity
            )
            prefix = (
                OBSERVATION_PREFIX
                if isinstance(command, DeclareRepairTools)
                else SELECTION_PREFIX
                if isinstance(command, SelectRepairTools)
                else TRAINING_PREFIX
                if isinstance(command, DeclareArmouryTraining)
                else DEFAULT_PREFIX
                if isinstance(command, SelectRepairDefault)
                else TIME_PREFIX
                if isinstance(command, SelectRepairTime)
                else PARTS_PREFIX
                if isinstance(command, AssessRepairParts)
                else PREFIX
            )
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
            if isinstance(
                command,
                (AssessRepairParts, SelectRepairTime, SelectRepairDefault, SelectRepairTools),
            )
            else (Seats(initial), Trusted(play.engine.reviewer.gm_ids), ActsAs(command.actor_id)),
            rng=play.rng,
        )
        return await submit(play, cid, plan, principal_id=principal_id)
