"""B375 admission for the existing single held B249 Fireball carrier."""

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.generations import missile_interposition_enabled
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def validate_fireball(
    state: PlayState, pending: PendingDefense, *, require_roll: bool = True
) -> None:
    effect = latest(state.resources).get(pending.spell_cast_id or "")
    if (
        effect is None
        or effect.phase != "active"
        or effect.spell_id != "fireball"
        or not effect.execute_effects
        or effect.actor_id != pending.attacker_id
    ):
        raise ValidationError("Interposition requires the attacker's active held Fireball")
    if require_roll and (
        pending.attack_roll is None
        or not pending.attack_roll.outcome.succeeded
        or pending.attack_roll.outcome is Outcome.CRITICAL_SUCCESS
    ):
        raise ValidationError("Interposition requires a successful noncritical missile attack")


def capture_attack(runtime: RulesContext, state: PlayState, encounter: Encounter) -> Encounter:
    """Resolve the enemy's actual attack before the friend chooses a defense."""
    pending = encounter.pending_defense
    if not missile_interposition_enabled() or pending is None or pending.target_item_id:
        return encounter
    effect = latest(state.resources).get(pending.spell_cast_id or "")
    if effect is None or not effect.execute_effects:
        return encounter
    validate_fireball(state, pending, require_roll=False)
    # deferred: the established missile reducer owns source modifiers and RNG.
    from wayfarer.engine.simulation.magic.missiles import resolve

    trace = resolve(runtime, state, encounter, "none", None, None, None, prepare_only=True).roll(
        runtime.rng
    )
    return encounter.model_copy(
        update={"pending_defense": pending.model_copy(update={"attack_roll": trace})}
    )
