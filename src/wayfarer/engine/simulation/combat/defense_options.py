"""B375/B377 optional dodge branches, validated before server-owned randomness."""

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def prepare_options(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense,
    *,
    resolve: bool,
) -> Encounter:
    if not command.acrobatic_dodge and not command.dodge_and_drop:
        return encounter
    if (
        runtime.rules.combat is None
        or runtime.rules.combat.gurps_equipment is None
        or runtime.rules.combat.gurps_equipment.profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Optional dodge requires Basic Set combat")
    if command.defense != "dodge" or command.second_defense is not None:
        raise ValidationError("Optional dodge requires one Dodge defense")
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    defender = pending.defender_id if pending else unarmed.target_id if unarmed else None
    if defender != command.actor_id:
        raise ValidationError("Optional dodge is unavailable to this actor")
    target = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if target.maneuver_state.defense_forbidden:
        raise ValidationError("Optional dodge is forbidden by the maneuver")
    if command.dodge_and_drop:
        if target.personal_flight is not None and target.personal_flight.altitude > 0:
            raise ValidationError("Aerial Dodge and Drop requires an authored concealment step")
        if command.retreat is not None or command.basic_retreat or target.posture == "prone":
            raise ValidationError("Dodge and Drop requires a standing or kneeling ranged defender")
        if (
            pending is None
            or pending.mode_id is None
            or not isinstance(
                mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id),
                RangedMode,
            )
        ):
            raise ValidationError("Dodge and Drop is only effective against ranged attacks")
    compiled = build(runtime, state, target.actor_id)
    if command.acrobatic_dodge:
        if target.acrobatic_dodge_trace is not None:
            raise ValidationError("Acrobatic Dodge was already attempted this turn")
        if not any(
            p.definition_id == "skill:acrobatics" and p.amount >= 1 for p in compiled.purchases
        ):
            raise ValidationError("Acrobatic Dodge requires a purchased Acrobatics skill")
        skill = int(level(compiled, "skill:acrobatics").value)
    if not resolve:
        return encounter
    bonus = 3 if command.dodge_and_drop else 0
    if command.acrobatic_dodge:
        trace = success_roll("gurps-basic-set-4e-2004", skill, rng=runtime.rng)
        bonus += 2 if trace.outcome.succeeded else -2
        target = target.model_copy(update={"acrobatic_dodge_trace": trace})
    return CombatEngine._replace(
        encounter,
        target.model_copy(update={"tactical_defense_bonus": target.tactical_defense_bonus + bonus}),
    )
