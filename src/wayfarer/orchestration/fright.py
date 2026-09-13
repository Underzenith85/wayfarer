"""Director-only care and panic decisions using the existing campaign CAS ledger."""

import json

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.fright_transitions import FrightDecision as FrightDecision
from wayfarer.engine.simulation.health.fright_transitions import apply_decision
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class FrightService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self, play: PlayService, state: PlayState, command: FrightDecision, *, principal_id: str
    ) -> CommandPlan[None]:
        """What a fright decision writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {
                "operation": "fright-decision",
                "principal": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated = apply_decision(before, command, play.rng)
            play.commit(campaign, updated)
            return CommandReceipt(action="npc", outcome="fright decision recorded")

        async def outcome(campaign: Campaign) -> None:
            return None

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(
                Seats(state, refusal="Fright decisions require director authority"),
                Trusted(play.engine.reviewer.gm_ids),
            ),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> None:
        try:
            command = FrightDecision.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid fright decision") from exc
        play = self.play.for_campaign(await self.play.store.read(cid))
        state = play._load(await play.store.read(cid))
        await submit(
            play,
            cid,
            self.plan(play, state, command, principal_id=principal_id),
            principal_id=principal_id,
        )
