"""Trusted enchanting and staff facts on the canonical command transaction."""

import hashlib
import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic import haste_wearable_construction as wearables
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    COMMAND_ADAPTER,
    CreateEnchantment,
    EnchantmentOutcome,
    TypedEnchantmentCommand,
    apply_enchantment,
    command_payload,
)
from wayfarer.engine.simulation.magic.haste_manufacture import (
    OBSERVATION,
    HasteManufacture,
    ObserveHasteManufacture,
    facts,
    observe,
)
from wayfarer.engine.simulation.magic.haste_state import record
from wayfarer.engine.simulation.magic.staff_state import (
    DeclareStaffConstruction,
    StaffConstruction,
    declare,
    identifier,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.enchantment_generations import (
    current_energy,
    current_haste_generation,
    current_settlement,
)
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class EnchantmentService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: TypedEnchantmentCommand
        | DeclareStaffConstruction
        | ObserveHasteManufacture
        | wearables.DeclareHasteWearableConstruction,
        *,
        principal_id: str,
        correct_settlement: bool = True,
        correct_energy: bool = True,
        haste_manufacture: bool = False,
        manufacture_generation: int = 1,
    ) -> CommandPlan[
        EnchantmentOutcome | StaffConstruction | HasteManufacture | wearables.WearableConstruction
    ]:
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Enchanting requires the exact Basic Set profile")
        if type(manufacture_generation) is not int or manufacture_generation not in (1, 2):
            raise ValidationError("Unsupported Haste manufacture generation")
        payload = json.dumps(
            {
                "operation": "haste-wearable-construction"
                if isinstance(command, wearables.DeclareHasteWearableConstruction)
                else "haste-manufacture"
                if isinstance(command, ObserveHasteManufacture)
                else "staff-construction"
                if isinstance(command, DeclareStaffConstruction)
                else "enchantment",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json")
                if isinstance(
                    command,
                    (
                        DeclareStaffConstruction,
                        ObserveHasteManufacture,
                        wearables.DeclareHasteWearableConstruction,
                    ),
                )
                else command_payload(command),
                **({"enchantment_settlement_generation": 1} if correct_settlement else {}),
                **({"enchantment_energy_generation": 1} if correct_energy else {}),
                **(
                    {"haste_manufacture_generation": manufacture_generation}
                    if haste_manufacture and isinstance(command, CreateEnchantment)
                    else {}
                ),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, wearables.DeclareHasteWearableConstruction):
                resources = wearables.declare(play.rules_context, before, command).model_copy(
                    update={"revision": before.revision + 1}
                )
                updated = before.model_copy(
                    update={"revision": before.revision + 1, "resources": resources}
                )
                result = "haste-wearable-construction"
            elif isinstance(command, ObserveHasteManufacture):
                fact = observe(play.rules_context, before, command)
                resources = record(
                    before.resources, OBSERVATION, command.id, command.target_item_id, fact
                ).model_copy(update={"revision": before.revision + 1})
                updated = before.model_copy(
                    update={"revision": before.revision + 1, "resources": resources}
                )
                result = "haste-manufacture"
            elif isinstance(command, DeclareStaffConstruction):
                resources = declare(before.resources, command).model_copy(
                    update={"revision": before.revision + 1}
                )
                updated = before.model_copy(
                    update={"revision": before.revision + 1, "resources": resources}
                )
                result = "staff-construction"
            else:
                updated, receipt = apply_enchantment(
                    play.rules_context,
                    before,
                    command,
                    system=True,
                    correct_settlement=correct_settlement,
                    correct_energy=correct_energy,
                    haste_manufacture=haste_manufacture,
                    manufacture_generation=manufacture_generation,
                )
                result = receipt.status
            play.commit(campaign, play.checkpoint(updated, before=before))
            return CommandReceipt(action="resource", outcome="enchantment:" + result)

        async def outcome(
            campaign: Campaign,
        ) -> (
            EnchantmentOutcome
            | StaffConstruction
            | HasteManufacture
            | wearables.WearableConstruction
        ):
            resource_state = play._load(campaign).resources
            if isinstance(command, wearables.DeclareHasteWearableConstruction):
                return wearables.WearableConstruction.model_validate_json(
                    next(
                        e.kind
                        for e in resource_state.events
                        if e.id == wearables.identifier(command.id)
                    )
                )
            if isinstance(command, ObserveHasteManufacture):
                return HasteManufacture.model_validate_json(
                    next(
                        e.kind
                        for e in resource_state.events
                        if e.id == OBSERVATION + hashlib.sha256(command.id.encode()).hexdigest()
                    )
                )
            if isinstance(command, DeclareStaffConstruction):
                return StaffConstruction.model_validate_json(
                    next(e.kind for e in resource_state.events if e.id == identifier(command.id))
                )
            return EnchantmentOutcome.model_validate_json(
                next(e.kind for e in resource_state.events if e.id == "enchantment:" + command.id)
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids)),
            rng=play.rng,
        )

    async def _submit(
        self,
        cid: str,
        command: TypedEnchantmentCommand
        | DeclareStaffConstruction
        | ObserveHasteManufacture
        | wearables.DeclareHasteWearableConstruction,
        principal_id: str,
    ) -> EnchantmentOutcome | StaffConstruction | HasteManufacture | wearables.WearableConstruction:
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        generation = (
            await current_haste_generation(
                play,
                cid,
                command.id,
                fresh=next(
                    (
                        2 if f.wearable is not None else 1
                        for f in facts(play._load(campaign).resources)
                        if f.project_id is None
                        and isinstance(command, CreateEnchantment)
                        and f.recipe_id == command.recipe_id
                        and f.target_item_id == command.target_item_id
                    ),
                    0,
                ),
            )
            if isinstance(command, CreateEnchantment)
            else 0
        )
        return await submit(
            play,
            cid,
            self.plan(
                play,
                play._load(campaign),
                command,
                principal_id=principal_id,
                correct_settlement=await current_settlement(play, cid, command.id),
                correct_energy=await current_energy(play, cid, command.id),
                haste_manufacture=generation != 0,
                manufacture_generation=generation or 1,
            ),
            principal_id=principal_id,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> EnchantmentOutcome:
        result = await self._submit(cid, COMMAND_ADAPTER.validate_python(value), principal_id)
        assert isinstance(result, EnchantmentOutcome)
        return result

    async def declare_staff(
        self, cid: str, value: object, *, principal_id: str
    ) -> StaffConstruction:
        result = await self._submit(
            cid, DeclareStaffConstruction.model_validate(value), principal_id
        )
        assert isinstance(result, StaffConstruction)
        return result

    async def observe_haste_manufacture(
        self, cid: str, value: object, *, principal_id: str
    ) -> HasteManufacture:
        result = await self._submit(
            cid, ObserveHasteManufacture.model_validate(value), principal_id
        )
        assert isinstance(result, HasteManufacture)
        return result

    async def declare_haste_wearable(
        self, cid: str, value: object, *, principal_id: str
    ) -> wearables.WearableConstruction:
        result = await self._submit(
            cid, wearables.DeclareHasteWearableConstruction.model_validate(value), principal_id
        )
        assert isinstance(result, wearables.WearableConstruction)
        return result
