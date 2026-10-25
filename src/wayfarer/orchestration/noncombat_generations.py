"""Private admission generations for authored noncombat checks."""

from typing import TYPE_CHECKING

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


async def _generation(play: PlayService, cid: str, command_id: str, key: str, label: str) -> bool:
    replay = recorded_command.get()
    prior = None if replay is not None else await play.store.command_input(cid, command_id)
    if replay is None and prior is None:
        return True
    text = replay.command_input if replay is not None else prior.text if prior is not None else None
    if text is None:
        return False
    payload = validation.mapping(replay_payload(text))
    generation = payload.get(key)
    if key in payload and (type(generation) is not int or generation != 1):
        raise ValidationError(f"Unsupported recorded noncombat {label} generation")
    return generation == 1


async def enchanting_rest(play: PlayService, cid: str, command_id: str) -> bool:
    return await _generation(play, cid, command_id, "enchanting_rest_generation", "enchanting-rest")


async def minimum_effective_skill(play: PlayService, cid: str, command_id: str) -> bool:
    enabled = await _generation(
        play, cid, command_id, "minimum_effective_skill_generation", "minimum-effective-skill"
    )
    return enabled and play.engine.reviewer.compiler.statistics_profile == "gurps-basic-set-4e-2004"


async def skill_prerequisite_context(play: PlayService, cid: str, command_id: str) -> bool:
    enabled = await _generation(
        play, cid, command_id, "skill_prerequisite_context_generation", "skill-prerequisite-context"
    )
    return enabled and play.engine.reviewer.compiler.statistics_profile == "gurps-basic-set-4e-2004"
