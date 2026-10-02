"""B375/B377 optional dodge branches, validated before server-owned randomness."""

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.combat.acrobatic_bonuses import modifiers
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.incoming import incoming_ranged
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _repeat_drop(encounter: Encounter, command: ChooseDefense) -> Encounter:
    pending = encounter.pending_defense
    target = next((p for p in encounter.participants if p.actor_id == command.actor_id), None)
    if (
        pending
        and target
        and target.drop_attacker_id == pending.attacker_id
        and command.defense != "none"
    ):
        return CombatEngine._replace(
            encounter,
            target.model_copy(update={"tactical_defense_bonus": target.tactical_defense_bonus + 3}),
        )
    return encounter


def prepare_options(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense,
    *,
    resolve: bool,
) -> Encounter:
    if not command.acrobatic_dodge and not command.dodge_and_drop:
        return _repeat_drop(encounter, command)
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
        if pending is None or not incoming_ranged(runtime, state, encounter, pending):
            raise ValidationError("Dodge and Drop is only effective against ranged attacks")
    compiled = build(runtime, state, target.actor_id)
    if command.acrobatic_dodge:
        if target.acrobatic_dodge_trace is not None:
            raise ValidationError("Acrobatic Dodge was already attempted this turn")
        skill_id = (
            "skill:aerobatics"
            if target.personal_flight is not None and target.personal_flight.altitude > 0
            else "skill:acrobatics"
        )
        if not any(p.definition_id == skill_id and p.amount >= 1 for p in compiled.purchases):
            raise ValidationError(
                f"Acrobatic Dodge requires a purchased {skill_id.split(':')[1].title()} skill"
            )
        skill = int(level(compiled, skill_id).value)
    if not resolve:
        return encounter
    bonus = (
        3
        if command.dodge_and_drop
        or pending is not None
        and target.drop_attacker_id == pending.attacker_id
        else 0
    )
    if command.dodge_and_drop and pending is not None:
        target = target.model_copy(update={"drop_attacker_id": pending.attacker_id})
    if command.acrobatic_dodge:
        trace = success_roll(
            "gurps-basic-set-4e-2004", skill, modifiers(compiled, skill_id), rng=runtime.rng
        )
        bonus += 2 if trace.outcome.succeeded else -2
        target = target.model_copy(update={"acrobatic_dodge_trace": trace})
    return CombatEngine._replace(
        encounter,
        target.model_copy(update={"tactical_defense_bonus": target.tactical_defense_bonus + bonus}),
    )
