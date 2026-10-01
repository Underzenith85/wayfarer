"""B124/B394 sensory admission before attack or defense resource and dice use."""

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.physical_defenses import physical_defenses
from wayfarer.engine.simulation.combat.visibility import combat_visibility
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def blind_hit_location(
    state: PlayState,
    actor_id: str,
    location: HitLocation | None,
    *,
    target_item_id: str | None = None,
    armor_chink: bool = False,
) -> HitLocation | None:
    """An unseen person can only be struck at a randomly determined location."""
    if not acute_blindness(state.resources, actor_id):
        return location
    if location not in (None, "random") or target_item_id is not None or armor_chink:
        raise ValidationError(
            "Blind attacks require random hit location, without aimed object or chink"
        )
    return "random"


def _current_pending_aim(
    state: PlayState, pending: PendingDefense
) -> tuple[HitLocation | None, bool]:
    """Unrolled body aim loses precision after onset; a recorded attack is history."""
    if (
        pending.attack_roll is not None
        or pending.suppression_zone_id is not None
        or pending.shield_rush
    ):
        return pending.hit_location, pending.armor_chink
    if acute_blindness(state.resources, pending.attacker_id) and pending.target_item_id is None:
        return "random", False
    return blind_hit_location(
        state,
        pending.attacker_id,
        pending.hit_location,
        target_item_id=pending.target_item_id,
        armor_chink=pending.armor_chink,
    ), pending.armor_chink


def refresh_armed_senses(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    """Admit against current senses before any retreat, exertion or defense dice.

    A previously recorded attack roll remains historical. Retreat may invalidate
    future location evidence, but cannot undo this command's admitted defense.
    """
    pending = encounter.pending_defense
    if pending is None:
        return encounter
    sensory = combat_visibility(
        encounter,
        pending.attacker_id,
        pending.defender_id,
        state=state,
        validate_attack=pending.attack_roll is None and pending.suppression_zone_id is None,
    )
    location, armor_chink = _current_pending_aim(state, pending)
    if command.sacrificial_for is not None:
        if acute_blindness(state.resources, command.actor_id):
            raise ValidationError(
                "Blind interposition requires a supported nonvisual interception procedure"
            )
        # The original attack must remain valid before another actor steps in.
        # prepare_interposition will validate its specialized attack shape and
        # the protector's sight/step; the ordinary resolver scores that actor.
        return encounter.model_copy(
            update={
                "pending_defense": pending.model_copy(
                    update={
                        "hit_location": location,
                        "armor_chink": armor_chink,
                        "visibility_attack_penalty": pending.visibility_attack_penalty
                        if pending.attack_roll is not None
                        or pending.suppression_zone_id is not None
                        else sensory.attack_penalty,
                        "allowed": ("none", "dodge"),
                    }
                )
            }
        )
    incoming = (
        None
        if pending.spell_cast_id is not None
        else mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    )
    physical = physical_defenses(runtime, state, encounter, pending, incoming)
    allowed = tuple(d for d in physical if d == "none" or d in sensory.defenses)
    for selected in (command.defense, command.second_defense):
        if selected is not None and selected not in allowed:
            raise ValidationError(
                "Selected defense is unavailable with current sensory and physical conditions"
            )
    # The pending offer is also copied into durable DefenseChoice history. Keep
    # its original advertised choices when a later condition removes eligibility:
    # the current check above, not that historical offer, admits this command.
    # Recovery can admit a choice absent from the original offer; record those
    # newly available choices so the ordinary resolver and history stay valid.
    recorded_allowed = pending.allowed + tuple(d for d in allowed if d not in pending.allowed)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "allowed": recorded_allowed,
                    "hit_location": location,
                    "armor_chink": armor_chink,
                    "visibility_attack_penalty": pending.visibility_attack_penalty
                    if pending.attack_roll is not None or pending.suppression_zone_id is not None
                    else sensory.attack_penalty,
                    "visibility_defense_penalty": sensory.defense_penalty,
                }
            )
        }
    )
