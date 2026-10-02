"""Trusted enchanting and staff facts on the canonical command transaction."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    COMMAND_ADAPTER,
    EnchantmentOutcome,
    TypedEnchantmentCommand,
    apply_enchantment,
)
from wayfarer.engine.simulation.magic.staff_state import (
    DeclareStaffConstruction,
    StaffConstruction,
    declare,
    identifier,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.enchantment_generations import current_settlement
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class EnchantmentService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: TypedEnchantmentCommand | DeclareStaffConstruction,
        *,
        principal_id: str,
        correct_settlement: bool = True,
    ) -> CommandPlan[EnchantmentOutcome | StaffConstruction]:
        if play.engine.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Enchanting requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "staff-construction"
                if isinstance(command, DeclareStaffConstruction)
                else "enchantment",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
                **({"enchantment_settlement_generation": 1} if correct_settlement else {}),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            if isinstance(command, DeclareStaffConstruction):
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
                )
                result = receipt.status
            play.commit(campaign, play.checkpoint(updated, before=before))
            return CommandReceipt(action="resource", outcome="enchantment:" + result)

        async def outcome(campaign: Campaign) -> EnchantmentOutcome | StaffConstruction:
            resource_state = play._load(campaign).resources
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
        command: TypedEnchantmentCommand | DeclareStaffConstruction,
        principal_id: str,
    ) -> EnchantmentOutcome | StaffConstruction:
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                play._load(campaign),
                command,
                principal_id=principal_id,
                correct_settlement=await current_settlement(play, cid, command.id),
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
