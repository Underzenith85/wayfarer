"""Private current-GM combat sensory authority through the shared CAS pipeline.

No transport exposes these commands. Their typed input is enough to replay the
Hearing roll without an external resolver; proof and rolls stay in private events.
"""

import json

from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.sensory_host import (
    ADAPTER,
    CombatSenseCommand,
    RevokeCombatSense,
    resolve,
)
from wayfarer.engine.simulation.combat.sensory_state import (
    CombatSensoryEvidence,
    checkpoint,
    event_id,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.pipeline import CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService


class CombatSensesService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: CombatSenseCommand,
        *,
        principal_id: str,
    ) -> CommandPlan[CombatSensoryEvidence | None]:
        payload = json.dumps(
            {
                "operation": "combat-senses",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        def reduce(campaign: Campaign) -> CommandReceipt:
            current = play._load(campaign)
            updated, _ = resolve(play.rules_context, current, command, principal_id=principal_id)
            updated = checkpoint(play.checkpoint(updated, before=current), before=current)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="combat:sensory-adjudication")

        async def outcome(campaign: Campaign) -> CombatSensoryEvidence | None:
            # Original private evidence remains available after its scope is invalidated.
            if isinstance(command, RevokeCombatSense):
                return None
            stored = play._load(campaign)
            return CombatSensoryEvidence.model_validate_json(
                next(e.kind for e in stored.resources.events if e.id == event_id(command.id))
            )

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

    async def execute(
        self, cid: str, value: object, *, principal_id: str
    ) -> CombatSensoryEvidence | None:
        try:
            command = ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid combat sensory adjudication") from exc
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        state = play._load(campaign)
        return await submit(
            play,
            cid,
            self.plan(play, state, command, principal_id=principal_id),
            principal_id=principal_id,
        )
