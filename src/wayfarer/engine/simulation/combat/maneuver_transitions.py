"""Authoritative maneuver checks: Basic Set B364-366 (numeric 2004 baseline)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def observe(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> Encounter:
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    target = next(p for p in encounter.participants if p.actor_id == command.target_id)
    if command.maneuver == "aim":
        item = next((i for i in state.resources.items if i.id == command.item_id), None)
        if item is None or item.owner_id != actor.actor_id or not item.ready or not item.equipped:
            raise ValidationError("Aim requires an owned ready weapon")
        entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
        modes = [
            m
            for m in entry.modes
            if isinstance(m, RangedMode) and (command.mode_id is None or m.id == command.mode_id)
        ]
        if len(modes) != 1:
            raise ValidationError("Aim requires one selected ranged mode")
        aimed_mode = modes[0]
        if aimed_mode.rated_strength is not None:
            stats = build(runtime, state, actor.actor_id).statistics
            assert stats is not None
            fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
            validate_rated_strength(
                catalog(runtime).profile_id, aimed_mode, fatigue_value(fp, stats.st)
            )
        hands = tuple(hand for item_id, hand in actor.hand_bindings if item_id == item.id)
        if command.braced:
            if aimed_mode.brace_kind == "one-handed" and set(hands) != {
                "left-hand",
                "right-hand",
            }:
                raise ValidationError("One-handed weapon bracing requires both hands")
            if aimed_mode.brace_kind == "bipod" and actor.posture != "prone":
                raise ValidationError("Bipod bracing requires a prone shooter")
            if aimed_mode.brace_kind is None:
                raise ValidationError("Selected ranged mode cannot be braced")
            if aimed_mode.hands == 2 and (
                command.destination is not None
                or command.hex_path
                or command.hex_facing is not None
                or command.posture is not None
            ):
                raise ValidationError("A braced two-handed weapon does not permit a step")
        previous = next(
            p
            for e in state.encounters
            if e.id == encounter.id
            for p in e.participants
            if p.actor_id == actor.actor_id
        )
        seconds = actor.maneuver_state.aim_seconds
        if previous.maneuver_state.aim_mode_id != aimed_mode.id:
            seconds = 1
        sight_bonus = (
            aimed_mode.scope_bonus
            if aimed_mode.fixed_power_scope and seconds >= aimed_mode.scope_bonus
            else 0
            if aimed_mode.fixed_power_scope
            else min(aimed_mode.scope_bonus, seconds)
        )
        return CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "maneuver_state": actor.maneuver_state.model_copy(
                        update={
                            "aim_accuracy": aimed_mode.accuracy,
                            "aim_mode_id": aimed_mode.id,
                            "aim_seconds": seconds,
                            "aim_braced": command.braced,
                            "aim_sight_bonus": min(aimed_mode.accuracy, sight_bonus),
                        }
                    )
                }
            ),
        )
    weapon = mode(runtime, state, actor.actor_id, command.item_id or "", command.mode_id)
    if not isinstance(weapon, MeleeMode):
        raise ValidationError("Feint requires a melee mode")
    attacker = build(runtime, state, actor.actor_id)
    defender = build(runtime, state, target.actor_id)
    assert defender.statistics is not None
    defense = defender.statistics.dx
    for item in state.resources.items:
        if item.owner_id != target.actor_id or not item.ready or not item.equipped:
            continue
        entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
        skills = [m.skill_id for m in entry.modes if isinstance(m, MeleeMode)]
        if entry.shield:
            skills.append(entry.shield.skill_id)
        for skill in skills:
            try:
                defense = max(defense, int(level(defender, skill).value))
            except ValidationError:
                continue
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    value = attack_modifier(
        actor.maneuver_state, target.actor_id, int(level(attacker, weapon.skill_id).value)
    )
    if target.unarmed_guard_dropped and actor.maneuver_state.evaluate_target_id == target.actor_id:
        value += actor.maneuver_state.evaluate_bonus
    value -= hp.injury.shock if hp.injury else 0
    first = success_roll(
        catalog(runtime).profile_id,
        value,
        check_modifiers(state.resources, actor.actor_id, "dx"),
        rng=runtime.rng,
    )
    second = success_roll(
        catalog(runtime).profile_id,
        defense,
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


def distracted(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    defended: bool,
    injured: bool,
) -> Encounter:
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    commitment = actor.maneuver_state
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    if commitment.aim_seconds and (
        defended
        or (
            injured
            and not success_roll(
                catalog(runtime).profile_id,
                compiled.statistics.will,
                check_modifiers(state.resources, actor_id, "will"),
                rng=runtime.rng,
            ).outcome.succeeded
        )
    ):
        commitment = commitment.model_copy(
            update={"aim_seconds": 0, "aim_item_id": None, "aim_target_id": None}
        )
    if commitment.concentrating and (defended or injured):
        if not success_roll(
            catalog(runtime).profile_id,
            compiled.statistics.will - 3,
            check_modifiers(state.resources, actor_id, "will"),
            rng=runtime.rng,
        ).outcome.succeeded:
            commitment = commitment.model_copy(
                update={"concentrating": False, "concentration_seconds": 0}
            )
    return CombatEngine._replace(encounter, actor.model_copy(update={"maneuver_state": commitment}))
