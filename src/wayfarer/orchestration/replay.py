"""Replay supported typed command families with recorded inputs and no providers."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.events import CommandRecord
from wayfarer.persistence.replay import command_text, unavailable_reason
from wayfarer.simulation.spells import SpellCommand


async def execute_recorded(play: PlayService, record: CommandRecord) -> None:
    """The caller supplies an isolated store containing the pre-command state.

    Unknown command families fail explicitly; replay never falls back to text
    interpretation, a provider, or a previously stored result.
    """
    reason = unavailable_reason(record)
    if reason:
        raise ValidationError(reason)
    payload = validation.mapping(validation.decode(command_text(record)))
    raw = payload.get("command", payload)
    if isinstance(raw, str):
        raw = validation.decode(raw)
    command = validation.mapping(raw)
    encoded = json.dumps(command)
    operation = payload.get("operation")
    with replay_inputs(record):
        if operation == "typed-action":
            await play.execute(record.campaign_id, command, authenticated_actor_id=record.actor_id)
        elif operation == "combat":
            await CombatService(play).execute(
                record.campaign_id,
                COMBAT_ADAPTER.validate_json(encoded),
                authenticated_actor_id=record.actor_id,
            )
        elif operation == "scene":
            await SceneService(play).execute(
                record.campaign_id,
                SCENE_ADAPTER.validate_json(encoded),
                authenticated_actor_id=record.actor_id,
            )
        elif operation == "party":
            await PartyService(play).execute(
                record.campaign_id,
                PartyCommand.model_validate_json(encoded),
                authenticated_actor_id=record.actor_id,
            )
        elif operation == "spell-lifecycle":
            state = play._load(await play.store.read(record.campaign_id))
            member = next(m for m in state.members if m.principal_id == record.actor_id)
            await SpellService(play).execute(
                record.campaign_id,
                SpellCommand.model_validate_json(encoded),
                authenticated_gm_id=record.actor_id if member.role == "gm" else None,
                principal_id=record.actor_id if member.role != "gm" else None,
            )
        elif record.event["action"] == "recovery":
            await RecoveryService(play).execute(
                record.campaign_id,
                RecoveryCommand.model_validate_json(encoded),
                authenticated_actor_id=record.actor_id,
            )
        else:
            raise ValidationError(f"No replay handler for command family {json.dumps(operation)}")
