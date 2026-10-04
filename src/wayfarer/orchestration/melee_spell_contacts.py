"""Capture private charged contact from the actual accepted physical attack."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.magic.hand_melee_contacts import (
    attach_contact as attach_hand_contact,
)
from wayfarer.engine.simulation.magic.hand_melee_contacts import (
    prepare_contact as prepare_hand_contact,
)
from wayfarer.engine.simulation.magic.melee_contact_dispatch import attach_contact, prepare_contact
from wayfarer.engine.simulation.rules_context import RulesContext


def capture_pending_contact(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command_id: str
) -> PlayState:
    unarmed = encounter.pending_unarmed
    if unarmed is not None:
        hand_contact = prepare_hand_contact(
            runtime, state, encounter, unarmed, command_id=command_id
        )
        if hand_contact is None:
            return state
        return state.model_copy(
            update={"resources": attach_hand_contact(state.resources, unarmed.id, hand_contact)}
        )
    pending = encounter.pending_defense
    if pending is None:
        return state
    contact = prepare_contact(
        runtime,
        state,
        command_id=command_id,
        pending_id=pending.id,
        encounter_id=encounter.id,
        attacker_id=pending.attacker_id,
        defender_id=pending.defender_id,
        carrier_item_id=pending.weapon_id,
        mode_id=pending.mode_id,
        requested_location=pending.hit_location,
    )
    if contact is None:
        return state
    return state.model_copy(
        update={"resources": attach_contact(state.resources, pending.id, contact)}
    )
