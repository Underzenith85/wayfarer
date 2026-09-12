"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    COMBAT_ADAPTER,
    ContinueCriticalMiss,
    DeclareBasicSpatialFacts,
    DeclareThrownLanding,
    EndEncounter,
    JoinEncounter,
    MigrateEncounterBasic,
    MigrateEncounterHex,
    ResolveWeaponExplosion,
    SetEncounterOpposition,
    StartBasicEncounter,
    StartEncounter,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import (
    CombatContext,
    _bind_combat_command,
    encounter_for,
)
from wayfarer.orchestration.combat.steps import reduce_combat
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class CombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def _recorded_result(self, cid: str, command_id: str) -> CombatResult:
        for entry in await self.play.store.history(cid):
            if entry.command_id == command_id:
                return CombatResult.model_validate_json(entry.event["outcome"])
        raise ValidationError("Missing committed combat receipt")

    @staticmethod
    def propose(value: object) -> TypedCombatCommand:
        try:
            return COMBAT_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid combat command") from exc

    @staticmethod
    def _encounter(state: PlayState, encounter_id: str) -> Encounter:
        return encounter_for(state, encounter_id)

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> CombatResult:
        command = self.propose(value)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Combat command actor is not authorized")
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await CombatService(bound).execute(
                cid, value, authenticated_actor_id=authenticated_actor_id
            )
        engine = self.play.engine.combat
        if engine is None:
            raise ValidationError("Campaign combat is not configured")
        if isinstance(
            command,
            (
                StartEncounter,
                StartBasicEncounter,
                DeclareBasicSpatialFacts,
                EndEncounter,
                MigrateEncounterBasic,
                MigrateEncounterHex,
                ContinueCriticalMiss,
                DeclareThrownLanding,
                ResolveWeaponExplosion,
                SetEncounterOpposition,
            ),
        ) and (command.actor_id not in self.play.engine.reviewer.gm_ids):
            raise ValidationError("Encounter lifecycle requires GM authority")
        if (
            isinstance(command, JoinEncounter)
            and command.joining_actor_id is not None
            and command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("GM admission requires GM authority")
        payload = json.dumps(
            {"operation": "combat", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.play.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            return await self._recorded_result(cid, command.id)

        def resolve(campaign: Campaign) -> CommandReceipt:
            play, before, effective_command = _bind_combat_command(campaign, command, self.play)
            updated, result = reduce_combat(before, effective_command, CombatContext(play, before))
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome=result.model_dump_json())

        committed = await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
        )
        if committed["kind"] == "replayed":
            return await self._recorded_result(cid, command.id)
        result = (
            self.play.for_campaign(committed["state"])._load(committed["state"]).last_combat_result
        )
        if result is None:
            raise ValidationError("Missing committed combat result")
        return result
