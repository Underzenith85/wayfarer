"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
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
    TakeCombatTurn,
    TakeUnarmedTurn,
    TypedCombatCommand,
    WithdrawEncounter,
)
from wayfarer.engine.simulation.combat.encounter import (
    CombatResult,
    Encounter,
    basic_visible,
)
from wayfarer.engine.simulation.combat.visibility import visible_actors
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

    @staticmethod
    def _basic_visible(encounter: Encounter, subject_id: str, object_id: str) -> bool:
        try:
            return basic_visible(encounter, subject_id, object_id)
        except ValidationError:
            return False

    def precheck(
        self, state: PlayState, member: CampaignMember, command: TypedCombatCommand
    ) -> None:
        """What a tactical caller may declare, before the engine is asked.

        The tactical route ran this itself; it is combat policy, so it lives with
        combat. #638 folds it into the family's preconditions.
        """
        if state.lifecycle != "active":
            raise ValidationError("Resume the campaign before acting")
        encounter = (
            None
            if isinstance(command, StartBasicEncounter)
            else self._encounter(state, command.encounter_id)
        )
        if isinstance(command, StartBasicEncounter):
            if member.role != "gm":
                raise ValidationError("Combat setup requires GM authority")
        elif isinstance(
            command,
            (
                MigrateEncounterHex,
                MigrateEncounterBasic,
                DeclareBasicSpatialFacts,
                ContinueCriticalMiss,
                DeclareThrownLanding,
                ResolveWeaponExplosion,
            ),
        ):
            if member.role != "gm":
                raise ValidationError("GM combat workflow requires GM authority")
        elif isinstance(command, WithdrawEncounter):
            assert encounter is not None
            if command.actor_id not in encounter.turn_order:
                raise ValidationError("Combat withdrawal is unavailable")
        elif isinstance(command, JoinEncounter):
            assert encounter is not None
            if command.joining_actor_id is not None and member.role != "gm":
                raise ValidationError("GM admission requires GM authority")
        else:
            assert encounter is not None
            if (
                encounter.spatial_kind not in ("basic", "hex")
                or command.actor_id not in encounter.turn_order
            ):
                raise ValidationError("Tactical encounter is unavailable")
            if encounter.spatial_kind == "basic":
                visible = frozenset(
                    participant.actor_id
                    for participant in encounter.participants
                    if participant.actor_id == command.actor_id
                    or self._basic_visible(encounter, command.actor_id, participant.actor_id)
                )
            else:
                visible = visible_actors(
                    state,
                    encounter,
                    command.actor_id,
                    board=self.play.rules_context.hex_map(encounter),
                )
            if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)):
                if command.target_id is not None and command.target_id not in visible:
                    raise ValidationError("Target is unavailable")
                if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
                    trigger = command.wait_trigger
                    if any(
                        a is not None and a not in visible
                        for a in (trigger.actor_id, trigger.target_id, trigger.reaction_target_id)
                    ):
                        raise ValidationError("Target is unavailable")

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
            self.play,
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
