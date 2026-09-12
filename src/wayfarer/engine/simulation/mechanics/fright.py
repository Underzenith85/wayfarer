"""State transitions for adjudicated fright care and panic decisions."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.fright import aftermath_modifiers, effects, public_id, save
from wayfarer.engine.simulation.resources import Command
from wayfarer.errors import ConflictError, ValidationError


class FrightDecision(Command):
    kind: Literal["care", "panic-response"]
    fright_id: str
    care: bool | None = None
    response: str | None = Field(default=None, min_length=1, max_length=1000)


def apply_decision(before: PlayState, command: FrightDecision, rng: RandomSource) -> PlayState:
    item = next(
        (
            i
            for i in effects(before.resources)
            if command.fright_id in (i.id, public_id(i))
            and i.actor_id == command.actor_id
            and i.active
        ),
        None,
    )
    if item is None:
        raise ConflictError("No active fright consequence for this actor")
    resources = before.resources
    if command.kind == "care":
        if not item.effect.neglect_progression or command.care is None or command.response:
            raise ValidationError("Care decision requires active catatonia and a care value")
        if item.due is not None and item.due <= resources.game_time:
            raise ConflictError("Settle due catatonia before changing care")
        item = item.model_copy(update={"care": command.care})
    else:
        if item.effect.table_total != 33 or command.response is None or command.care is not None:
            raise ValidationError("Panic response requires an explicit row-33 adjudication")
        # Record a completed, externally adjudicated response, not a forced player action.
        check = success_roll(
            "gurps-basic-set-4e-2004",
            item.recovery_target,
            aftermath_modifiers(resources, command.actor_id),
            rng=rng,
        )
        passed = check.outcome.succeeded
        effect = item.effect
        if not passed:
            effect = effect.model_copy(
                update={
                    "panic_severity": sum(draw_dice(rng, 3)),
                }
            )
        item = item.model_copy(
            update={
                "active": not passed,
                "effect": effect,
                "panic_responses": item.panic_responses + (command.response,),
                "recovery_checks": item.recovery_checks + (check,),
            }
        )
    resources = save(resources, item, command.id).model_copy(
        update={"revision": before.revision + 1}
    )
    updated = before.model_copy(update={"revision": before.revision + 1, "resources": resources})
    return updated
