"""Private enchanting settlement generation with unchanged historical retries."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest


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


async def current_haste_generation(
    play: PlayService, cid: str, command_id: str, *, fresh: int
) -> int:
    replay = recorded_command.get()
    prior = None if replay is not None else await play.store.command_input(cid, command_id)
    if replay is None and prior is None:
        return fresh
    text = replay.command_input if replay is not None else prior.text if prior is not None else None
    if text is None:
        return 0
    generation = validation.mapping(replay_payload(text)).get("haste_manufacture_generation")
    if generation is None:
        return 0
    if type(generation) is not int or generation not in (1, 2):
        raise ValidationError("Unsupported recorded Haste manufacture generation")
    return generation


WORK_ADMISSION_KEY = "enchantment_work_admission_generation"


async def current_work_admission(
    play: PlayService, cid: str, command_id: str, *, eligible: bool
) -> bool:
    replay = recorded_command.get()
    saved = (
        CommandInput(replay.payload_hash, replay.command_input)
        if replay is not None
        else await play.store.command_input(cid, command_id)
    )
    if saved is None:
        return eligible
    if saved.text is None:
        return False
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded enchanting work-admission input does not match its digest")
    payload = validation.mapping(replay_payload(saved.text))
    if WORK_ADMISSION_KEY not in payload:
        return False
    value = payload[WORK_ADMISSION_KEY]
    command = validation.mapping(payload.get("command"))
    if (
        type(value) is not int
        or value != 1
        or not eligible
        or payload.get("operation") != "enchantment"
        or command.get("kind") != "begin"
    ):
        raise ValidationError("Unsupported recorded enchanting work-admission generation")
    return True
