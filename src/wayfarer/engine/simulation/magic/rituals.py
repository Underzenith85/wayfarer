"""B237 ordinary casting rituals, using current approved and physical facts."""

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.movement_forms import movement_forms
from wayfarer.engine.rules.magic.protocols import ritual_requirements
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.objects.locations import unavailable_hand
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.ritual_state import latest
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _free_hands(state: PlayState, actor_id: str, encounter: Encounter | None) -> int:
    if encounter is not None:
        # deferred: the combat maneuver dispatcher also imports spell state.
        from wayfarer.engine.simulation.combat.unarmed.fighters import free_hands

        return len(free_hands(state, encounter, actor_id))
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    held = {
        i.id
        for i in state.resources.items
        if i.owner_id == actor_id and i.equipped and i.ready and is_carried(state.resources, i)
    }
    occupied = {hand for item, hand in actor.held_item_hands if item in held}
    unavailable = disabled(state.resources, actor_id)
    return sum(
        hand not in occupied and not unavailable_hand(unavailable, hand)
        for hand in ("left-hand", "right-hand")
    )


def require_ordinary_ritual(
    runtime: RulesContext, state: PlayState, actor_id: str, compiled: ValidatedBuild, skill: int
) -> None:
    """Skill here is trained skill adjusted only for low mana, never range/shock.

    Gestures at skill10+ need not be made with an empty hand: B237 does not
    impose that restriction. Ambiguous restraint and speech facts come from
    private current-director observations, never inferred narrative text.
    """
    requirements = ritual_requirements(max(1, skill))
    if requirements.gesture == "none":
        return
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    encounter = next(
        (e for e in state.encounters if e.status == "active" and actor_id in e.turn_order), None
    )
    participant = (
        next((p for p in encounter.participants if p.actor_id == actor_id), None)
        if encounter
        else None
    )
    capability = latest(state.resources, actor_id)
    if capability is not None and capability.build_revision != compiled.revision:
        raise ValidationError("Refresh ritual capability facts after an approved body/build change")
    observation = capability.command if capability else None
    unavailable = disabled(state.resources, actor_id)
    forms = movement_forms(compiled, runtime.reviewer.compiler.definitions)
    hands = {
        hand for hand in ("left-hand", "right-hand") if not unavailable_hand(unavailable, hand)
    }
    if forms.has("disadvantage:no-manipulators"):
        hands.clear()
    if encounter:
        hands -= {
            "left-hand" if g.location == "left-arm" else "right-hand"
            for g in encounter.grips
            if g.target_id == actor_id and g.location in ("left-arm", "right-arm")
        }
    # Occupied hands are not automatically unable to gesture (B237). Generic
    # restraints do not specify whether fingers/head can move; ask the current
    # director for a typed observation instead of inventing that anatomy.
    gesture = observation.gesture_available if observation else None
    if gesture is None and observation and observation.full_body_free:
        gesture = True
    if (
        gesture is None
        and hands
        and "restrained" not in actor.conditions
        and not (participant and participant.pinned)
    ):
        gesture = True
    speech = not any(
        p.definition_id in ("trait:disadvantage:cannot-speak", "trait:disadvantage:mute")
        for p in compiled.purchases
    ) and not (observation and observation.speech_available is False)
    if requirements.gesture == "small" and speech:
        return
    if requirements.gesture != "small" and not speech:
        raise ValidationError("B237 requires speech for this ordinary ritual")
    if gesture is None:
        raise ValidationError("Declare the caster's B237 gesture capability")
    if not gesture:
        raise ValidationError("B237 requires an available gesture")
    if requirements.gesture != "full-body":
        return
    if "restrained" in actor.conditions and not (observation and observation.full_body_free):
        raise ValidationError("Declare whether the B237 full-body ritual is free")
    if (observation and observation.full_body_free is False) or participant and participant.pinned:
        raise ValidationError("B237 requires free full-body ritual movement")
    blocked_feet = bool(unavailable & {"left-leg", "right-leg", "left-foot", "right-foot"})
    if encounter is not None:
        blocked_feet |= any(
            g.target_id == actor_id and g.location in ("left-leg", "right-leg")
            for g in encounter.grips
        )
    if (
        _free_hands(state, actor_id, encounter) != 2
        or forms.has("disadvantage:no-manipulators")
        or forms.has("disadvantage:no-legs")
        or blocked_feet
    ):
        raise ValidationError("B237 requires both hands and both feet free")
