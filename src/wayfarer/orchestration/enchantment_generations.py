"""Private enchanting settlement generation with unchanged historical retries."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload


async def _current_generation(
    play: PlayService, cid: str, command_id: str, key: str, *, fresh: bool = True
) -> bool:
    replay = recorded_command.get()
    if replay is not None:
        text = replay.command_input
    else:
        prior = await play.store.command_input(cid, command_id)
        if prior is None:
            return fresh
        text = prior.text
    if text is None:
        return False
    payload = validation.mapping(replay_payload(text))
    generation = payload.get(key)
    if generation is not None and (type(generation) is not int or generation != 1):
        raise ValidationError("Unsupported recorded enchanting generation")
    return generation == 1


async def current_settlement(play: PlayService, cid: str, command_id: str) -> bool:
    return await _current_generation(play, cid, command_id, "enchantment_settlement_generation")


async def current_energy(play: PlayService, cid: str, command_id: str) -> bool:
    return await _current_generation(play, cid, command_id, "enchantment_energy_generation")


async def current_haste_manufacture(
    play: PlayService, cid: str, command_id: str, *, eligible: bool
) -> bool:
    return await _current_generation(
        play, cid, command_id, "haste_manufacture_generation", fresh=eligible
    )
