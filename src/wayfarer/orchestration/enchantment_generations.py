"""Private enchanting settlement generation with unchanged historical retries."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload


async def current_settlement(play: PlayService, cid: str, command_id: str) -> bool:
    replay = recorded_command.get()
    if replay is not None:
        text = replay.command_input
    else:
        prior = await play.store.command_input(cid, command_id)
        if prior is None:
            return True
        text = prior.text
    if text is None:
        return False
    payload = validation.mapping(replay_payload(text))
    generation = payload.get("enchantment_settlement_generation")
    if generation not in (None, 1):
        raise ValidationError("Unsupported recorded enchanting settlement generation")
    return generation == 1
