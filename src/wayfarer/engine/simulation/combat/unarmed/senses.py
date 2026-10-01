"""Acute Symptoms integration; preserve pre-existing sighted unarmed behavior."""

from wayfarer.engine.rules.types.tactical import CombatVisibility
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.visibility import combat_visibility
from wayfarer.engine.simulation.health.symptom_state import acute_blindness


def visibility(
    state: PlayState,
    encounter: Encounter,
    attacker_id: str,
    defender_id: str,
    *,
    validate_attack: bool = True,
) -> CombatVisibility:
    attacker_blind = acute_blindness(state.resources, attacker_id)
    if not attacker_blind and not acute_blindness(state.resources, defender_id):
        return CombatVisibility()
    # Ordinary attacks already passed declaration geometry. A Basic close entry
    # can invalidate its sight facts, but never supplies a blind defender's
    # nonvisual awareness. A blind attacker still needs current target evidence.
    return combat_visibility(
        encounter,
        attacker_id,
        defender_id,
        state=state,
        validate_attack=validate_attack and attacker_blind,
    )
