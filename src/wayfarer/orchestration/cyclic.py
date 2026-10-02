"""Private, seated and deployment-trusted GM Cyclic observations."""

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.cyclic_host_state import (
    ADAPTER,
    CyclicHostCommand,
    CyclicHostReceipt,
    history,
)
from wayfarer.engine.simulation.health.cyclic_observations import resolve
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class CyclicService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: CyclicHostCommand,
        *,
        principal_id: str,
    ) -> CommandPlan[CyclicHostReceipt]:
        payload = json.dumps(
            {
                "operation": "cyclic-host",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = resolve(
                play.rules_context, before, command, principal_id=principal_id
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> CyclicHostReceipt:
            result = next(
                (r for r in history(play._load(campaign).resources) if r.command_id == command.id),
                None,
            )
            if result is None:
                raise ConflictError("Cyclic observation receipt is missing")
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=reduce,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids)),
            rng=play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> CyclicHostReceipt:
        try:
            command = ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid Cyclic observation") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, play._load(campaign), command, principal_id=principal_id),
            principal_id=principal_id,
        )
