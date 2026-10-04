"""B365: the Feint in an unarmed All-Out Attack precedes its single attack."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, skill_value
from wayfarer.engine.simulation.combat.unarmed.records import BASIC
from wayfarer.engine.simulation.equipment.catalog import MeleeMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def defense_skill(runtime: RulesContext, state: PlayState, target: Combatant) -> int:
    if active_effect(state.resources, target.actor_id) is not None:
        raise ValidationError("Rooted Feet combined unarmed Feint resistance is unsupported")
    compiled = build(runtime, state, target.actor_id)
    assert compiled.statistics is not None
    skills = {
        "skill:brawling",
        "skill:boxing",
        "skill:karate",
        "skill:judo",
        "skill:wrestling",
        "skill:sumo-wrestling",
    }
    for item in state.resources.items:
        if item.owner_id != target.actor_id or item.id not in target.ready_item_ids:
            continue
        entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
        skills.update(m.skill_id for m in entry.modes if isinstance(m, MeleeMode))
        if entry.shield is not None:
            skills.add(entry.shield.skill_id)
    value = compiled.statistics.dx
    for skill in skills:
        try:
            value = max(value, int(level(compiled, skill).value))
        except ValidationError:
            continue
    return value


def feint(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> Encounter:
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    if any(
        active_effect(state.resources, actor_id) is not None
        for actor_id in (actor.actor_id, target.actor_id)
    ):
        raise ValidationError("Rooted Feet combined unarmed Feint classification is unsupported")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    assert hp.injury is not None
    value = attack_modifier(
        actor.maneuver_state,
        target.actor_id,
        skill_value(runtime, state, actor.actor_id, command.skill) - hp.injury.shock,
    )
    if target.unarmed_guard_dropped and actor.maneuver_state.evaluate_target_id == target.actor_id:
        value += actor.maneuver_state.evaluate_bonus
    first = success_roll(
        BASIC, value, check_modifiers(state.resources, actor.actor_id, "dx"), rng=runtime.rng
    )
    second = success_roll(
        BASIC,
        defense_skill(runtime, state, target),
        check_modifiers(state.resources, target.actor_id, "dx", defensive=True),
        rng=runtime.rng,
    )
    penalty = max(0, first.margin - max(0, second.margin)) if first.outcome.succeeded else 0
    return CombatEngine._replace(
        encounter,
        actor.model_copy(
            update={
                "maneuver_state": actor.maneuver_state.model_copy(
                    update={
                        "feint_target_id": target.actor_id,
                        "feint_penalty": penalty,
                        "feint_rolls": (first, second),
                        "evaluate_bonus": 0,
                        "evaluate_target_id": None,
                    }
                )
            }
        ),
    )
