"""Private ordinary random unarmed strikes; no public tactical schema changes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.unarmed.random_strike import RandomStrike, pending_id, save
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedSkill
from wayfarer.engine.simulation.resources import Command
from wayfarer.errors import ValidationError
from wayfarer.models import Id
from wayfarer.orchestration.combat.context import CombatContext, encounter_for
from wayfarer.orchestration.combat.generations import KEY, capture
from wayfarer.orchestration.combat.service import CombatService
from wayfarer.orchestration.combat.steps import reduce_combat
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class RandomUnarmedStrike(Command):
    operation: Literal["random-unarmed-strike"] = "random-unarmed-strike"
    encounter_id: Id
    target_id: Id
    action: Literal["punch", "kick"]
    skill: UnarmedSkill = "attribute:dx"
    hands: tuple[Hand, ...] = ()
    foot: Literal["left-foot", "right-foot"] = "right-foot"

    def turn(self) -> TakeUnarmedTurn:
        # Torso is a valid legacy placeholder, never a chosen/resolved location.
        return TakeUnarmedTurn(**self.model_dump(exclude={"operation"}))


class RandomUnarmedService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, cid: str, command: RandomUnarmedStrike, *, features: frozenset[str] = frozenset()
    ) -> CommandPlan[CombatResult]:
        turn = command.turn()
        payload = json.dumps(
            {
                "operation": "combat-random-unarmed",
                "command": command.model_dump(mode="json"),
                **({KEY: sorted(features)} if features else {}),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = self.play._load(campaign)
            encounter = encounter_for(before, command.encounter_id)
            if encounter.wait_interrupt is not None or any(
                participant.maneuver_state.wait is not None
                for participant in encounter.participants
            ):
                raise ValidationError("Random unarmed strikes during Wait are not supported")
            prepared = save(
                before,
                RandomStrike(
                    command_id=command.id,
                    encounter_id=command.encounter_id,
                    actor_id=command.actor_id,
                    target_id=command.target_id,
                    pending_id=pending_id(command.id),
                ),
            )
            with combat_generation(features):
                updated, result = reduce_combat(prepared, turn, CombatContext(self.play, before))
            updated = self.play.checkpoint(updated, before=before)
            self.play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> CombatResult:
            result = self.play._load(campaign).last_combat_result
            if result is None:
                raise ValidationError("Missing committed random-unarmed result")
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
            command = RandomUnarmedStrike.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid private random unarmed strike") from exc
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await RandomUnarmedService(bound).execute(cid, value, principal_id=principal_id)
        features = await capture(self.play.store, cid, command.id)
        return await submit(
            self.play, cid, self.plan(cid, command, features=features), principal_id=principal_id
        )
