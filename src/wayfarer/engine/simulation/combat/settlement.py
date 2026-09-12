"""Combat eligibility and settlement at a resolved command boundary.

B419-420 and B426: nonpositive HP/FP alone does not incapacitate a fighter.
Fatigue collapse is incapacitation even when the injury record is unaffected.
The fewer-than-two stopping policy is separate from these rules (issue #568).
"""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import fatigue_ready
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def combat_ready(state: PlayState, actor_id: str, *, gurps: bool) -> bool:
    """Read persisted incapacity; never roll a new consciousness/exertion check."""
    hp_id = f"hp:{actor_id}"
    hp = next((p for p in state.resources.pools if p.id == hp_id), None)
    if not gurps:
        return hp is not None and (
            not hp.injury.incapacitated if hp.injury is not None else hp.current > 0
        )
    if hp is None or hp.injury is None:
        raise ValidationError("Combat requires explicit GURPS injury state", reference=hp_id)
    fp_id = f"fp:{actor_id}"
    fp = next((p for p in state.resources.pools if p.id == fp_id), None)
    if fp is None or fp.fatigue is None:
        raise ValidationError("Combat requires explicit GURPS fatigue state", reference=fp_id)
    if hp.injury.profile_id != fp.fatigue.profile_id:
        raise ValidationError("Combat requires matching HP and FP profiles", reference=fp_id)

    return not hp.injury.incapacitated and fatigue_ready(state, actor_id)


def settle_encounter(runtime: RulesContext, state: PlayState, encounter: Encounter) -> Encounter:
    """Settle once after either a turn or defense, preserving pending interactions."""
    engine = runtime.combat
    assert isinstance(engine, CombatEngine)
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.blocked_reason is not None
    ):
        return encounter
    gurps = engine.rules.gurps_equipment is not None
    if not gurps and not engine.rules.attacks:
        return encounter
    ready = {
        actor_id for actor_id in encounter.turn_order if combat_ready(state, actor_id, gurps=gurps)
    }
    if len(ready) < 2:
        return encounter.model_copy(
            update={
                "status": "completed",
                "completion_reason": "incapacitation",
                "wait_interrupt": None,
            }
        )
    while encounter.current_actor_id not in ready:
        encounter = engine._advance(encounter)
    return encounter
