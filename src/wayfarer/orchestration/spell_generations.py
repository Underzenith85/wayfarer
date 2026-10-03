"""Private spell generations preserve recorded checks and exact live retries."""

from dataclasses import dataclass

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.replay import command_text


@dataclass(frozen=True)
class SpellGenerations:
    capture_targeting: bool = True
    check_symptoms: bool = True
    item_sight: bool = True
    area_targeting: bool = True
    missile_attack: bool = True


async def recorded_generations(
    play: PlayService, state: PlayState, command: RuntimeSpellCommand
) -> SpellGenerations:
    prior = recorded_command.get()
    if prior is None and any(r.command_id == command.id for r in state.resources.receipts):
        prior = next(
            (r for r in await play.store.history(state.campaign_id) if r.command_id == command.id),
            None,
        )
    if prior is None:
        return SpellGenerations()
    if prior.command_input is None:
        return SpellGenerations(False, False, False, False, False)
    payload = validation.mapping(validation.decode(command_text(prior)))
    targeting = payload.get("targeting_generation")
    checks = payload.get("check_generation")
    sight = payload.get("item_sight_generation")
    area = payload.get("area_targeting_generation")
    missile = payload.get("missile_attack_generation")
    if "missile_attack_generation" in payload and (
        payload.get("operation") != "spell-lifecycle" or type(missile) is not int or missile != 1
    ):
        raise ValidationError("Unsupported recorded missile attack generation")
    if targeting not in (None, 1):
        raise ValidationError("Unsupported recorded spell targeting generation")
    if checks not in (None, 1):
        raise ValidationError("Unsupported recorded spell check generation")
    if sight not in (None, 1):
        raise ValidationError("Unsupported recorded item sight generation")
    if area not in (None, 1):
        raise ValidationError("Unsupported recorded Area targeting generation")
    return SpellGenerations(targeting == 1, checks == 1, sight == 1, area == 1, missile == 1)
