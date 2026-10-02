"""Private, attacker-authorized completion of an unresolved pending attack."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack, abandon
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.traits.composed_host import AbandonComposedAttack
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep, encounter_for
from wayfarer.orchestration.combat.service import CombatService
from wayfarer.orchestration.combat.settlement import _finish_combat, _settle_combat
from wayfarer.orchestration.composed_attacks import (
    ComposedAttackService,
    recorded_operation,
)
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class AbandonPendingAttackService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(self, cid: str, command: AbandonPendingAttack) -> CommandPlan[CombatResult]:
        payload = json.dumps(
            {
                "operation": "combat-abandon-pending-attack",
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = self.play._load(campaign)
            encounter = encounter_for(before, command.encounter_id)
            context = CombatContext(self.play, before)
            state, encounter, result = abandon(self.play.rules_context, before, encounter, command)
            step = CombatStep(state, encounter, state.resources, result)
            encounters = tuple(encounter if e.id == encounter.id else e for e in state.encounters)
            step, encounters = _settle_combat(step, command, encounters, context)
            updated, result = _finish_combat(step, command, encounters, context)
            updated = self.play.checkpoint(updated, before=before)
            self.play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> CombatResult:
            result = self.play._load(campaign).last_combat_result
            if result is None:
                raise ValidationError("Missing committed attack-abandonment result")
            return result

        async def replayed(campaign: Campaign) -> CombatResult:
            return await CombatService(self.play)._recorded_result(cid, command.id)

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            replayed=replayed,
            control=(ActsAs(command.actor_id),),
            rng=self.play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> CombatResult:
        try:
            command = AbandonPendingAttack.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid private attack-abandonment command") from exc
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await AbandonPendingAttackService(bound).execute(
                cid, value, principal_id=principal_id
            )

        state = self.play._load(await self.play.store.read(cid))
        encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
        composed = bool(
            encounter and encounter.pending_defense and encounter.pending_defense.composed_attack_id
        )
        if composed or await recorded_operation(self.play, cid, command.id, "composed-attack"):
            result = await ComposedAttackService(self.play).execute(
                cid,
                AbandonComposedAttack(
                    id=command.id,
                    actor_id=command.actor_id,
                    expected_revision=command.expected_revision,
                    encounter_id=command.encounter_id,
                    pending_id=command.pending_id,
                ),
                principal_id=principal_id,
            )
            if result.combat is None:
                raise ValidationError("Missing composed abandonment result")
            return result.combat
        return await submit(self.play, cid, self.plan(cid, command), principal_id=principal_id)
