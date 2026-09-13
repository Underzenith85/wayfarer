"""Rolling the attack and the defense against it."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import strong_damage_bonus
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, exertion
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.tactical import height_effect
from wayfarer.engine.simulation.combat.unarmed.choke import start_choke_hold
from wayfarer.engine.simulation.combat.unarmed.defense import parry_candidates, unarmed_defense
from wayfarer.engine.simulation.combat.unarmed.fighters import (
    encumbrance_level,
    fighter,
    free_hands,
    skill_value,
)
from wayfarer.engine.simulation.combat.unarmed.injury import (
    armed_parry_injury,
    armor_dr,
    critical_miss,
    drop_held,
    hurt,
)
from wayfarer.engine.simulation.combat.unarmed.records import (
    BASIC,
    Grip,
    UnarmedTrace,
    striking_bonus,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.hit_locations import torso_near_miss
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import ChooseDefense
    from wayfarer.engine.simulation.rules_context import RulesContext


def defend(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> tuple[PlayState, Encounter, UnarmedTrace]:
    pending = encounter.pending_unarmed
    if (
        pending is None
        or command.actor_id != pending.target_id
        or command.defense not in pending.allowed
    ):
        raise ValidationError("Defense is not authorized for this unarmed attack")
    defense_target, hand = unarmed_defense(
        runtime,
        state,
        encounter,
        command.actor_id,
        command.defense,
        command.item_id,
        location=pending.location,
        mode_id=command.parry_mode_id,
    )
    actor, target = fighter(encounter, pending.actor_id), fighter(encounter, pending.target_id)
    defenses = [(defense_target, hand)]
    if command.second_defense is None:
        if command.second_item_id is not None or command.second_parry_mode_id is not None:
            raise ValidationError("Second defense equipment requires a second defense")
    else:
        if (
            command.defense == "none"
            or command.second_defense == "none"
            or target.maneuver_state.enhanced_defense != "double"
        ):
            raise ValidationError("Second defense requires All-Out Defense (Double)")
        if command.second_defense not in pending.allowed:
            raise ValidationError("Second defense is not available against this attack")
        second_target, second_hand = unarmed_defense(
            runtime,
            state,
            encounter,
            command.actor_id,
            command.second_defense,
            command.second_item_id,
            location=pending.location,
            mode_id=command.second_parry_mode_id,
        )
        if command.defense == command.second_defense and not (
            command.defense == "parry" and hand != second_hand
        ):
            raise ValidationError(
                "Double defense requires different defenses or different parrying hands"
            )
        defenses.append((second_target, second_hand))
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    assert hp.injury is not None
    value = skill_value(runtime, state, actor.actor_id, pending.skill) - hp.injury.shock
    value += hp.injury.physical_traits.darkness(encounter.darkness_penalty)
    if pending.skill in ("skill:judo", "skill:karate"):
        value -= encumbrance_level(runtime, state, actor.actor_id)
    value -= 4 if actor.grappled else 0
    value -= 4 if actor.posture == "prone" else 2 if actor.posture == "kneeling" else 0
    value -= 2 if pending.action == "kick" else 0
    value -= (
        {"torso": 0, "neck": 2, "left-arm": 1, "right-arm": 1, "left-leg": 1, "right-leg": 1}[
            pending.location
        ]
        if pending.action == "grapple" and not pending.choke_hold
        else 0
    )
    value -= int(pending.choke_hold) * {"skill:judo": 2, "skill:wrestling": 3}.get(pending.skill, 0)
    if pending.action in ("punch", "kick"):
        value -= {
            "torso": 0,
            "neck": 5,
            "left-arm": 2,
            "right-arm": 2,
            "left-leg": 2,
            "right-leg": 2,
        }[pending.location]

    if actor.maneuver_state.feint_target_id == target.actor_id:
        defenses = [
            (
                v - actor.maneuver_state.feint_penalty * (2 if target.unarmed_guard_dropped else 1)
                if v is not None
                else None,
                h,
            )
            for v, h in defenses
        ]
    if encounter.spatial_kind == "hex":
        value += height_effect(
            encounter,
            actor,
            target,
            reach=1,
            location=pending.location,
            board=runtime.hex_map(encounter),
        ).attack_modifier
    if target.unarmed_guard_dropped and actor.maneuver_state.evaluate_target_id == target.actor_id:
        value += actor.maneuver_state.evaluate_bonus
    value = attack_modifier(
        actor.maneuver_state,
        target.actor_id,
        value,
        check_adjustment=sum(
            m.value for m in check_modifiers(state.resources, actor.actor_id, "dx")
        ),
    )
    attack = success_roll(
        BASIC, value, check_modifiers(state.resources, actor.actor_id, "dx"), rng=runtime.rng
    )

    near_miss = pending.action in ("punch", "kick") and torso_near_miss(pending.location, attack)
    resolved_location = "torso" if near_miss else pending.location
    checks: tuple[CheckTrace, ...] = (attack,)
    hit = attack.outcome.succeeded or near_miss
    blocked = None
    table: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    effect_checks: tuple[CheckTrace, ...] = ()
    critical = 0
    if attack.outcome is Outcome.CRITICAL_SUCCESS:
        table = draw_dice(runtime.rng, 3)
        critical = sum(table)
    elif attack.outcome is Outcome.CRITICAL_FAILURE:
        # B557's unarmed critical-miss table is not the armed critical-miss table.
        table = draw_dice(runtime.rng, 3)
        state, encounter, effect_checks, effect_dice, handled = critical_miss(
            runtime, state, encounter, pending, actor.actor_id, table, None
        )
        actor = fighter(encounter, actor.actor_id)
        blocked = None if handled else f"basic-unarmed-critical:{attack.outcome.value}:{sum(table)}"
        hit = False
    if hit and defense_target is not None and not critical:
        state, can_defend = exertion(runtime, state, target.actor_id, command.id)
        if can_defend:
            for index, (defense_level, parrying_hand) in enumerate(defenses):
                assert defense_level is not None
                defense = success_roll(BASIC, defense_level, rng=runtime.rng)
                checks += (defense,)
                hit = not defense.outcome.succeeded
                if parrying_hand:
                    target = target.model_copy(
                        update={"parries": target.parries + (parrying_hand,)}
                    )
                if (
                    defense.outcome.succeeded
                    and parrying_hand in ("left-hand", "right-hand")
                    and len(free_hands(state, encounter, target.actor_id)) == 2
                ):
                    _, parry_skill = max(
                        parry_candidates(runtime, state, encounter, target.actor_id)
                    )
                    if parry_skill == "skill:judo":
                        next_round = encounter.round + int(
                            encounter.turn_order.index(target.actor_id) <= encounter.turn_index
                        )
                        target = target.model_copy(
                            update={
                                "unarmed_lock_opportunity": (
                                    actor.actor_id,
                                    parry_skill,
                                    next_round,
                                )
                            }
                        )
                if defense.outcome is Outcome.CRITICAL_FAILURE and parrying_hand is None:
                    # B382: a critical Dodge falls, without a critical-miss table roll.
                    target = target.model_copy(update={"posture": "prone"})
                elif defense.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS):
                    table = draw_dice(runtime.rng, 3)
                    subject = actor.actor_id if defense.outcome.succeeded else target.actor_id
                    encounter = CombatEngine._replace(encounter, target)
                    state, encounter, effect_checks, effect_dice, handled = critical_miss(
                        runtime,
                        state,
                        encounter,
                        pending,
                        subject,
                        table,
                        None if defense.outcome.succeeded else parrying_hand,
                        command.parry_mode_id if index == 0 else command.second_parry_mode_id,
                    )
                    actor, target = (
                        fighter(encounter, actor.actor_id),
                        fighter(encounter, target.actor_id),
                    )
                    blocked = (
                        None
                        if handled
                        else f"basic-unarmed-defense-critical:{defense.outcome.value}:{sum(table)}"
                    )
                    hit = handled and not defense.outcome.succeeded
                elif defense.outcome.succeeded and parrying_hand not in (
                    None,
                    "left-hand",
                    "right-hand",
                ):
                    assert parrying_hand is not None
                    state, encounter, effect_checks, effect_dice = armed_parry_injury(
                        runtime,
                        state,
                        encounter,
                        pending,
                        parrying_hand,
                        command.parry_mode_id if index == 0 else command.second_parry_mode_id,
                    )
                    actor = fighter(encounter, actor.actor_id)
                if not hit or defense.outcome is Outcome.CRITICAL_FAILURE:
                    break
            target = target.model_copy(
                update={
                    "maneuver_state": target.maneuver_state.model_copy(update={"defended": True})
                }
            )

            encounter = CombatEngine._replace(encounter, target)
            encounter = distracted(
                runtime, state, encounter, target.actor_id, defended=True, injured=False
            )
    if (
        pending.action == "kick"
        and attack.outcome is Outcome.FAILURE
        and not near_miss
        and blocked is None
    ):
        balance = success_roll(
            BASIC,
            skill_value(runtime, state, actor.actor_id, "attribute:dx"),
            check_modifiers(state.resources, actor.actor_id, "dx"),
            rng=runtime.rng,
        )
        checks += (balance,)
        if not balance.outcome.succeeded:
            actor = actor.model_copy(update={"posture": "prone"})
    encounter = CombatEngine._replace(encounter, actor)
    grip_id = None
    basic = injury = 0
    dice: tuple[int, ...] = ()
    if hit and pending.action in ("grapple", "arm_lock"):
        grip_id = pending.id
        grip = Grip(
            id=grip_id,
            holder_id=actor.actor_id,
            target_id=target.actor_id,
            hands=pending.hands,
            location=pending.location,
            skill=pending.skill,
            acquired_round=encounter.round,
            arm_lock=pending.action == "arm_lock",
            choke_hold=pending.choke_hold,
        )
        state, grip = start_choke_hold(runtime, state, encounter, grip, pending.id)
        encounter = encounter.model_copy(
            update={"grips": tuple(g for g in encounter.grips if g.id != pending.grip_id) + (grip,)}
        )
    elif hit:
        compiled = build(runtime, state, actor.actor_id)
        assert compiled.statistics is not None
        expression = compiled.statistics.thrust
        maximum = critical in (6, 15)
        dice = () if maximum else draw_dice(runtime.rng, expression.dice)
        bonus = striking_bonus(
            pending.skill,
            compiled.statistics.dx,
            skill_value(runtime, state, actor.actor_id, pending.skill),
        )
        basic = max(
            0,
            (6 * expression.dice if maximum else sum(dice))
            + expression.add
            + (-1 if pending.action == "punch" else 0)
            + bonus * expression.dice,
            # B365: Strong adds two damage or one per die, whichever is better.
        )
        if actor.maneuver_state.strong:
            basic = max(
                0,
                (6 * expression.dice if maximum else sum(dice))
                + expression.add
                + (-1 if pending.action == "punch" else 0)
                + bonus * expression.dice
                + strong_damage_bonus(expression.dice),
            )
        basic *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
        resistance = armor_dr(runtime, state, target.actor_id, resolved_location)
        state, encounter, injury = hurt(
            runtime,
            state,
            encounter,
            target.actor_id,
            pending.id,
            basic,
            location=resolved_location,
            critical=critical,
        )
        if resistance >= 3 and basic >= 5:
            striking_part = pending.hands[0] if pending.action == "punch" else pending.foot
            state, encounter, _ = hurt(
                runtime,
                state,
                encounter,
                actor.actor_id,
                "unarmed-self:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                min(resistance, basic // 5),
                location=striking_part,
            )
    if critical == 12:
        state, encounter = drop_held(state, encounter, target.actor_id)
    return (
        state,
        encounter,
        UnarmedTrace(
            action=pending.action,
            intent=pending,
            actor_id=actor.actor_id,
            target_id=target.actor_id,
            checks=checks,
            won=hit,
            grip_id=grip_id,
            basic_damage=basic,
            damage_dice=dice,
            injury=injury,
            blocked_reason=blocked,
            table_dice=table,
            effect_dice=effect_dice,
            effect_checks=effect_checks,
            resolved_location=resolved_location if pending.action in ("punch", "kick") else None,
            defenses=tuple(
                (choice, selected_hand)
                for choice, (_, selected_hand) in zip(
                    (command.defense, command.second_defense), defenses, strict=False
                )
                if choice is not None
            ),
        ),
    )
