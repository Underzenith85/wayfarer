"""Private B236 interpretations with actual lock-object consequences."""

import hashlib

from wayfarer.engine.simulation.magic.bindings import RuntimeBackfireAlternative
from wayfarer.engine.simulation.magic.lock_effects import apply_lock_effect, validate_lock_target
from wayfarer.engine.simulation.magic.lock_state import destroyed, latest, magelocks, save
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEffect,
    RuntimeSpellEvent,
    SpellResult,
    event_id,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PREFIX = "lock-backfire-choice:"


def choices(state: ResourceState) -> tuple[RuntimeBackfireAlternative, ...]:
    return tuple(
        RuntimeBackfireAlternative.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX)
    )


def declare(
    state: ResourceState, choice: RuntimeBackfireAlternative, command_id: str
) -> ResourceState:
    if choice.spell_id not in ("lockmaster", "magelock"):
        raise ValidationError("Private lock backfire choices require a lock spell")
    if any(c.id == choice.id for c in choices(state)):
        raise ConflictError("A lock backfire interpretation cannot be replaced")
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.game_time,
                    target_id="gm",
                    kind=choice.model_dump_json(),
                ),
            )
        }
    )


def validate_target(state: ResourceState, spell: str, target_id: str, *, reverse: bool) -> None:
    if reverse:
        value = latest(state).get(target_id)
        if value is None or destroyed(state, value):
            raise ValidationError("Reversed lock magic requires an authored object")
        if spell == "lockmaster" and (not value.fixture.mechanical_lock or not value.closed):
            raise ValidationError("Reversed Lockmaster requires a closed mechanical lock")
    else:
        validate_lock_target(state, spell, target_id)


def apply_effect(
    state: ResourceState,
    effect: RuntimeSpellEffect,
    choice: RuntimeBackfireAlternative,
    command_id: str,
    target_id: str,
) -> ResourceState:
    validate_target(state, effect.spell_id, target_id, reverse=choice.effect == "reverse")
    replacement = effect.model_copy(
        update={
            "target_id": target_id,
            "phase": "active",
            "expires_at": state.game_time + 21600 if effect.spell_id == "magelock" else None,
        }
    )
    if choice.effect == "reverse":
        value = latest(state)[target_id]
        if effect.spell_id == "lockmaster":
            value = value.model_copy(update={"locked": True})
        else:
            # B236 reversal of magical closing makes the door open. Existing
            # closures end; there is no duplicate permanent magical-lock flag.
            for ward in magelocks(state, target_id):
                state = state.model_copy(
                    update={
                        "events": state.events
                        + (
                            ResourceEvent(
                                id=event_id(command_id + ":reverse:" + ward.cast_id, ward.spell_id),
                                at=state.game_time,
                                target_id=target_id,
                                kind=RuntimeSpellEvent(
                                    effect=ward.model_copy(update={"phase": "ended"}),
                                    result=SpellResult(outcome="cancelled"),
                                ).model_dump_json(),
                            ),
                        )
                    }
                )
            value = value.model_copy(update={"locked": False, "closed": False})
        state = save(state, value, command_id)
        replacement = replacement.model_copy(update={"phase": "ended", "reversed": True})
    else:
        state, replacement = apply_lock_effect(state, replacement, command_id)
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id(command_id, effect.spell_id),
                    at=state.game_time,
                    target_id=target_id,
                    kind=RuntimeSpellEvent(
                        effect=replacement, result=SpellResult(outcome="active")
                    ).model_dump_json(),
                ),
            )
        }
    )
