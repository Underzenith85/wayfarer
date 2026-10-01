"""Current-GM lock fixture and approved-spell transactions on the shared ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.lock_host import (
    ADAPTER,
    LockCommand,
    LockReceipt,
    _receipt_id,
    apply_host,
)
from wayfarer.engine.simulation.magic.spell_state import event_id, parse_event
from wayfarer.engine.simulation.magic.spell_transitions import SpellExecutionContext, reduce_spell
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand, SpellResult
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_generations import recorded_generations


class LockService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: LockCommand, *, principal_id: str
    ) -> CommandPlan[LockReceipt]:
        if play.engine.reviewer.compiler.statistics_profile != PROFILE:
            raise ValidationError("Lock host requires the exact Basic Set profile")
        payload = json.dumps(
            {
                "operation": "lock-host",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, receipt = apply_host(play.rules_context, before, command)
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="lock:" + receipt.outcome)

        async def outcome(campaign: Campaign) -> LockReceipt:
            return LockReceipt.model_validate_json(
                next(
                    e.kind
                    for e in play._load(campaign).resources.events
                    if e.id == _receipt_id(command.id)
                )
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

    async def execute(self, cid: str, value: object, *, principal_id: str) -> LockReceipt:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )


class LockSpellService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: RuntimeSpellCommand,
        *,
        principal_id: str,
        check_symptoms: bool = True,
    ) -> CommandPlan[SpellResult]:
        if command.spell_id not in ("lockmaster", "magelock"):
            raise ValidationError("Lock host only executes Lockmaster and Magelock")
        payload = json.dumps(
            {
                "operation": "lock-spell",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
                **({"check_generation": 1} if check_symptoms else {}),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = reduce_spell(
                before,
                command,
                SpellExecutionContext(play.rules_context, check_symptoms=check_symptoms),
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="lock-spell:" + result.outcome)

        async def outcome(campaign: Campaign) -> SpellResult:
            return parse_event(
                next(
                    e
                    for e in play._load(campaign).resources.events
                    if e.id == event_id(command.id, command.spell_id)
                )
            ).result

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

    async def execute(self, cid: str, value: object, *, principal_id: str) -> SpellResult:
        command = RuntimeSpellCommand.model_validate(value)
        if command.spell_id not in ("lockmaster", "magelock"):
            raise ValidationError("Lock host only executes Lockmaster and Magelock")
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        generations = await recorded_generations(play, state, command)
        return await submit(
            play,
            cid,
            self.plan(
                play,
                state,
                command,
                principal_id=principal_id,
                check_symptoms=generations.check_symptoms,
            ),
            principal_id=principal_id,
        )
