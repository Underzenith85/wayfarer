"""Profile-selected weapon melee within the existing encounter transaction.

Numeric baseline: Lite (August 2004), pp. 24-28; Basic Set B369-376,
B381-382 and B556. Complex Basic critical-miss consequences stop runtime with a
persisted table result rather than silently substituting ordinary damage.
"""

from __future__ import annotations

import hashlib

from wayfarer.engine.rules.checks import Outcome, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty, strong_damage_bonus
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.entangle import attack_penalty as entangle_attack_penalty
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.hit_locations import (
    attack_penalty,
    missing_location,
    part,
    select_location,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def resolve_melee(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    *,
    second_defense: Defense | None = None,
    second_item_id: str | None = None,
    parry_mode_id: str | None = None,
    second_parry_mode_id: str | None = None,
    catch_thrown: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None
    if pending.spell_cast_id is not None:
        from wayfarer.engine.simulation.magic.missiles import resolve as resolve_spell

        return resolve_spell(
            runtime, state, encounter, selected, item_id, second_defense, second_item_id
        )
    equipment = catalog(runtime)
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode):
        from wayfarer.engine.simulation.combat.ranged.resolution import resolve

        return resolve(
            runtime,
            state,
            encounter,
            weapon,
            selected,
            item_id,
            second_defense=second_defense,
            second_item_id=second_item_id,
            parry_mode_id=parry_mode_id,
            second_parry_mode_id=second_parry_mode_id,
            catch_thrown=catch_thrown,
        )
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attack_build = build(runtime, state, pending.attacker_id)
    defend_build = build(runtime, state, pending.defender_id)
    assert attack_build.statistics is not None and defend_build.statistics is not None
    attack_value = level(attack_build, weapon.skill_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.defender_id}")
    attacker_hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.attacker_id}")
    attacker_fp = next(p for p in state.resources.pools if p.id == f"fp:{pending.attacker_id}")
    if hp.injury is None or attacker_hp.injury is None:
        raise ValidationError("GURPS injury pool requires explicit migration")
    defense_derived, defense_item = defense_value(
        runtime,
        state,
        defender,
        selected,
        item_id,
        incoming_item_id=pending.weapon_id,
        incoming_mode_id=pending.mode_id,
        parry_mode_id=parry_mode_id,
    )
    critical_parry_mode = parry_mode_id
    second_derived = None
    second_item = None
    if second_defense is not None:
        if (
            selected == "none"
            or second_defense == "none"
            or defender.maneuver_state.enhanced_defense != "double"
        ):
            raise ValidationError("Second defense requires All-Out Defense (Double)")
        second_derived, second_item = defense_value(
            runtime,
            state,
            defender,
            second_defense,
            second_item_id,
            incoming_item_id=pending.weapon_id,
            incoming_mode_id=pending.mode_id,
            parry_mode_id=second_parry_mode_id,
        )
        if second_defense == selected and not (selected == "parry" and second_item != defense_item):
            raise ValidationError(
                "Double defense requires different defenses or different parrying hands"
            )
    elif second_item_id is not None:
        raise ValidationError("Second defense equipment requires a second defense")
    attack_target = (
        int(attack_value.value)
        + attacker_hp.injury.physical_traits.darkness(encounter.darkness_penalty)
        - attacker_hp.injury.shock
        - minimum_strength_penalty(
            weapon.minimum_st, fatigue_value(attacker_fp, attack_build.statistics.st)
        )
    )
    from wayfarer.engine.simulation.combat.objects.combat import shock

    attack_target -= shock(state, pending.weapon_id)
    if pending.target_item_id:
        from wayfarer.engine.simulation.combat.objects.combat import target_modifier

        attack_target += target_modifier(
            runtime, state, pending.defender_id, pending.target_item_id
        )
    attack_target -= 4 if attacker.grappled else 0
    attack_target -= (
        4 if attacker.posture == "prone" else 2 if attacker.posture == "kneeling" else 0
    )
    from wayfarer.engine.simulation.health.hit_locations import disabled

    eyes = disabled(state.resources, pending.attacker_id) & {"left-eye", "right-eye"}
    attack_target -= 6 if len(eyes) == 2 else 1 if eyes else 0
    from wayfarer.engine.simulation.combat.tactical import height_effect

    height = height_effect(
        encounter,
        attacker,
        defender,
        reach=max(weapon.reach),
        location=pending.hit_location,
        board=runtime.hex_map(encounter),
    )
    attack_target += height.attack_modifier
    if pending.hit_location:
        entries = {e.definition_id: e for e in equipment.entries}
        shield_side = next(
            (
                hand.split("-")[0]
                for item, hand in defender.hand_bindings
                if any(
                    i.id == item and entries[i.definition_id].shield for i in state.resources.items
                )
            ),
            None,
        )
        attack_target += attack_penalty(pending.hit_location, shield_side=shield_side)
    attack_target += entangle_attack_penalty(attacker)
    if (
        defender.unarmed_guard_dropped
        and attacker.maneuver_state.evaluate_target_id == defender.actor_id
    ):
        attack_target += attacker.maneuver_state.evaluate_bonus
    attack_target = attack_modifier(
        attacker.maneuver_state,
        defender.actor_id,
        attack_target,
        check_adjustment=sum(
            m.value for m in check_modifiers(state.resources, attacker.actor_id, "dx")
        ),
    )
    if defense_derived is not None and attacker.maneuver_state.feint_target_id == defender.actor_id:
        defense_derived = DerivedValue(
            defense_derived.target,
            defense_derived.value
            - attacker.maneuver_state.feint_penalty * (2 if defender.unarmed_guard_dropped else 1),
            defense_derived.explanations,
        )
    attack = success_roll(
        equipment.profile_id,
        attack_target,
        check_modifiers(state.resources, attacker.actor_id, "dx"),
        rng=runtime.rng,
    )
    defense = None
    second_trace = None
    from wayfarer.engine.simulation.health.hit_locations import (
        location_special_effects,
        torso_near_miss,
    )

    near_miss = torso_near_miss(pending.hit_location, attack)
    if near_miss:
        try:
            height_effect(
                encounter,
                attacker,
                defender,
                reach=max(weapon.reach),
                location="torso",
                board=runtime.hex_map(encounter),
            )
        except ValidationError:
            near_miss = False
    hit = attack.outcome.succeeded or near_miss
    critical_dice: tuple[int, ...] = ()
    critical_tables: tuple[tuple[int, ...], ...] = ()
    critical = 0
    blocked = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    lasting_ids: tuple[str, ...] = ()
    if (
        attack.outcome is Outcome.CRITICAL_FAILURE
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = draw_dice(runtime.rng, 3)
        blocked = f"basic-critical-miss:{sum(critical_dice)}"
    from wayfarer.engine.simulation.combat.objects.combat import intercepting_shield

    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_derived is not None:
        defense = success_roll(equipment.profile_id, int(defense_derived.value), rng=runtime.rng)
        hit = not defense.outcome.succeeded
        if selected == "parry" and defense_item:
            defender = defender.model_copy(update={"parries": defender.parries + (defense_item,)})
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if (
            defense.outcome.succeeded
            and selected == "parry"
            and intercepting_shield(runtime, state, encounter, defense, require_durable=False)
            is None
        ):
            from wayfarer.engine.simulation.combat.melee_heavy_parry import resolve_heavy_parry

            assert defense_item is not None
            state, defender, parry_dice, stopped = resolve_heavy_parry(
                runtime, state, encounter, defender, defense_item
            )
            effect_dice += parry_dice
            if not stopped:
                hit = True
                blocked = None
        if equipment.profile_id == "gurps-basic-set-4e-2004" and (
            (defense.outcome is Outcome.CRITICAL_SUCCESS and not hit)
            or (selected == "parry" and defense.outcome is Outcome.CRITICAL_FAILURE)
        ):
            critical_dice = draw_dice(runtime.rng, 3)
            blocked = f"basic-critical-miss:{sum(critical_dice)}:{'attacker' if defense.outcome.succeeded else 'defender'}"
            hit = defense.outcome is Outcome.CRITICAL_FAILURE and sum(critical_dice) in (
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                16,
            )
        elif defense.outcome is Outcome.CRITICAL_FAILURE:
            if selected == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif selected == "block" and defense_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    i.model_copy(update={"ready": False})
                                    if i.id == defense_item
                                    else i
                                    for i in state.resources.items
                                )
                            }
                        )
                    }
                )
                defender = defender.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i for i in defender.ready_item_ids if i != defense_item
                        )
                    }
                )
    if (
        attack.outcome is Outcome.CRITICAL_SUCCESS
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = draw_dice(runtime.rng, 3)
        critical = sum(critical_dice)
    if hit and defense is not None and second_derived is not None and blocked is None:
        from wayfarer.engine.simulation.combat.objects.combat import defense_stress

        state, encounter = defense_stress(
            runtime,
            state,
            encounter,
            defender.actor_id,
            pending.id + ":second",
            second_item,
        )
        defender = defender.model_copy(
            update={
                "ready_item_ids": tuple(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == defender.actor_id and i.equipped and i.ready
                )
            }
        )
        try:
            second_derived, second_item = defense_value(
                runtime,
                state,
                defender,
                second_defense or "none",
                second_item,
                parry_mode_id=second_parry_mode_id,
            )
        except ValidationError:
            second_derived = None
    if hit and defense is not None and second_derived is not None and blocked is None:
        second_target = int(second_derived.value) - (
            attacker.maneuver_state.feint_penalty * (2 if defender.unarmed_guard_dropped else 1)
            if attacker.maneuver_state.feint_target_id == defender.actor_id
            else 0
        )
        second_trace = success_roll(equipment.profile_id, second_target, rng=runtime.rng)
        hit = not second_trace.outcome.succeeded
        if second_defense == "parry" and second_item:
            defender = defender.model_copy(update={"parries": defender.parries + (second_item,)})
        if second_defense == "block":
            defender = defender.model_copy(update={"block_used": True})
        if (
            second_trace.outcome.succeeded
            and second_defense == "parry"
            and intercepting_shield(runtime, state, encounter, second_trace, require_durable=False)
            is None
        ):
            from wayfarer.engine.simulation.combat.melee_heavy_parry import resolve_heavy_parry

            assert second_item is not None
            state, defender, parry_dice, stopped = resolve_heavy_parry(
                runtime, state, encounter, defender, second_item
            )
            effect_dice += parry_dice
            if not stopped:
                hit = True
                blocked = None
        if second_trace.outcome is Outcome.CRITICAL_FAILURE:
            if second_defense == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif second_defense == "parry":
                critical_dice = draw_dice(runtime.rng, 3)
                blocked = f"basic-critical-miss:{sum(critical_dice)}:defender"
                defense_item = second_item
                critical_parry_mode = second_parry_mode_id
                hit = sum(critical_dice) in (7, 8, 9, 10, 11, 12, 13, 14, 16)
            elif second_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    i.model_copy(update={"ready": False})
                                    if i.id == second_item
                                    else i
                                    for i in state.resources.items
                                )
                            }
                        )
                    }
                )
                defender = defender.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i for i in defender.ready_item_ids if i != second_item
                        )
                    }
                )
        elif (
            second_trace.outcome is Outcome.CRITICAL_SUCCESS
            and not hit
            and equipment.profile_id == "gurps-basic-set-4e-2004"
        ):
            critical_dice = draw_dice(runtime.rng, 3)
            blocked = f"basic-critical-miss:{sum(critical_dice)}:attacker"
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.engine.simulation.combat.criticals.limbs import resolve_limb

        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    defender if p.actor_id == defender.actor_id else p
                    for p in encounter.participants
                )
            }
        )
        parry_miss = blocked.endswith(":defender")
        state, encounter, limb = resolve_limb(
            runtime,
            state,
            encounter,
            table=critical_dice,
            defender_item=defense_item,
            blocker=blocked,
            defender_mode_id=critical_parry_mode,
        )
        critical_tables = limb.table_rolls
        critical_dice = critical_tables[-1]
        effect_dice += tuple(d for roll in critical_tables[1:] for d in roll)
        effect_dice += limb.location_dice + limb.damage_dice
        lasting_ids += limb.lasting_injury_ids
        attacker = next(p for p in encounter.participants if p.actor_id == attacker.actor_id)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        if limb.resolved:
            blocked = None
            hit = parry_miss
        elif len(critical_tables) > 1:
            suffix = (
                ":defender" if parry_miss else ":attacker" if blocked.endswith(":attacker") else ""
            )
            blocked = f"basic-critical-miss:{sum(critical_dice)}{suffix}"
            hit = parry_miss and sum(critical_dice) in (7, 8, 9, 10, 11, 12, 13, 14, 16)
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.engine.simulation.combat.objects.combat import critical_breakage

        parrying = blocked.endswith(":defender")
        state, encounter, object_dice, resolved = critical_breakage(
            runtime,
            state,
            encounter,
            table=critical_dice,
            defender_item=defense_item,
            parrying=parrying,
        )
        effect_dice += object_dice
        if resolved:
            blocked = None
            hit = parrying
        attacker = next(p for p in encounter.participants if p.actor_id == attacker.actor_id)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
    if hit and pending.hit_location:
        from wayfarer.engine.simulation.combat.objects.locations import from_behind

        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=runtime.rng,
            from_behind=from_behind(attacker, defender),
        )
        if pending.hit_location == "random" and missing_location(hp.injury, location):
            location = "torso"
    head = (
        location in ("face", "skull", "left-eye", "right-eye")
        and weapon.damage.damage_type != "tox"
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    if head and critical in (6, 7) and location in ("face", "skull"):
        from wayfarer.engine.simulation.combat.objects.locations import from_behind

        if from_behind(attacker, defender) or (hp.injury.tolerance and hp.injury.tolerance.no_eyes):
            critical = 4
        else:
            eye_die = draw_dice(runtime.rng, 1)[0]
            effect_dice += (eye_die,)
            location = "right-eye" if eye_die <= 3 else "left-eye"
            critical_eye = True
    if head and critical == 8:
        defender = defender.model_copy(update={"forced_do_nothing": True})
    expression = (
        attack_build.statistics.swing
        if weapon.damage.basis == "swing"
        else attack_build.statistics.thrust
    )
    dice_count = weapon.damage.dice or expression.dice
    adds = weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    adds += attacker.maneuver_state.stop_thrust_damage_bonus
    if attacker.maneuver_state.strong:
        adds += strong_damage_bonus(dice_count)
    from wayfarer.engine.simulation.combat.objects.combat import shield_damage

    shield_hit = intercepting_shield(runtime, state, encounter, second_trace or defense)
    maximum = critical in ((3, 15) if head else (6, 15)) or (
        equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
    )
    dice = draw_dice(runtime.rng, dice_count) if (hit or shield_hit) and not maximum else ()
    basic = (
        max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * dice_count if maximum else sum(dice)) + adds,
        )
        if hit or shield_hit
        else 0
    )
    basic *= (
        3
        if critical in ((18,) if head else (3, 18))
        else 2
        if critical in ((16,) if head else (5, 16))
        else 1
    )
    if shield_hit and not hit:
        state, encounter, basic = shield_damage(
            runtime, state, encounter, shield_hit, basic, weapon
        )
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        hit = basic > 0
        if hit and pending.hit_location:
            side_die = draw_dice(runtime.rng, 1)[0]
            effect_dice += (side_die,)
            # Preserve the original grip even when this impact disables the shield.
            original = next(
                p
                for e in state.encounters
                if e.id == encounter.id
                for p in e.participants
                if p.actor_id == defender.actor_id
            )
            hand = next((h for i, h in original.hand_bindings if i == shield_hit), None)
            if side_die <= 2 and hand:
                location = "left-arm" if hand == "left-hand" else "right-arm"
            else:
                location, location_dice = select_location(pending.hit_location, rng=runtime.rng)
    entries = {e.definition_id: e for e in equipment.entries}
    resistance = max(
        (
            e.armor.dr
            for i in state.resources.items
            if i.owner_id == pending.defender_id
            and i.equipped
            and (i.condition is None or not i.condition.disabled)
            for e in (entries[i.definition_id],)
            if e.armor
            and (
                (location or "torso") in e.armor.locations
                or (part(location) + "s" if location else "torso") in e.armor.locations
            )
        ),
        default=0,
    )
    from wayfarer.engine.simulation.abilities import damage_resistance

    if runtime.rules.abilities is not None:
        resistance += damage_resistance(
            state.resources, pending.defender_id, build_revision=defend_build.revision
        )
    half = critical in ((4, 5, 17) if head else (4, 17))
    injury = 0
    held = tuple(
        i.id
        for i in state.resources.items
        if i.id in defender.ready_item_ids
        and (entries[i.definition_id].modes or entries[i.definition_id].shield)
    )
    if hit and pending.target_item_id:
        from wayfarer.engine.simulation.combat.objects.combat import synchronize
        from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object

        resources, object_result = apply_object(
            runtime.resources,
            state.resources,
            DamageObject.model_validate(
                {
                    "id": "target-object:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                    "actor_id": pending.attacker_id,
                    "expected_revision": state.resources.revision,
                    "item_id": pending.target_item_id,
                    "basic_damage": basic,
                    "damage_type": weapon.damage.damage_type,
                    "armor_divisor": weapon.damage.armor_divisor * (2 if half else 1),
                }
            ),
            system=True,
            rng=runtime.rng,
        )
        state = state.model_copy(update={"resources": resources})
        encounter = synchronize(state, encounter)
        defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
        resistance = object_result.effective_dr
        effect_dice += tuple(d for roll in object_result.checks for d in roll)
    elif hit:
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=pending.id,
                actor_id=pending.defender_id,
                expected_revision=state.resources.revision,
                basic_damage=basic,
                resistance=resistance,
                damage_type=weapon.damage.damage_type,
                location=location,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
                critical_eye=critical_eye,
            ),
            ht=defend_build.statistics.ht,
            rng=runtime.rng,
            system=True,
            held_item_ids=held,
            held_item_locations=tuple((i, h) for i, h in defender.hand_bindings if i in held),
            shield_item_ids=tuple(
                i
                for i in held
                if entries[
                    next(item.definition_id for item in state.resources.items if item.id == i)
                ].shield
            ),
            dx=defend_build.statistics.dx,
            force_major_wound=critical in ((4, 5) if head else (7, 13, 14)),
            double_shock=critical == 8 and not head,
            funny_bone=critical == 8 and not head,
            halve_dr=("up" if head else "down") if half else None,
            ignore_dr=head and critical == 3,
            head_trauma=(
                "deafened"
                if head and critical in (12, 13) and weapon.damage.damage_type == "cr"
                else "scarred"
                if head and critical in (12, 13)
                else None
            ),
            scar_levels=2 if weapon.damage.damage_type in ("burn", "cor") else 1,
        )
        injury = result.injury
        resistance = result.effective_resistance
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
        state = state.model_copy(update={"resources": resources})
    updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
    status = updated_hp.injury
    assert status is not None
    drops = held if critical == 12 and not head and not pending.target_item_id else ()
    weapons = tuple(
        i
        for i in held
        if entries[next(item.definition_id for item in state.resources.items if item.id == i)].modes
    )
    if head and critical == 14 and weapons:
        if len(weapons) > 1:
            drop_die = draw_dice(runtime.rng, 1)[0]
            effect_dice += (drop_die,)
            drops = (weapons[0 if drop_die <= 3 else 1],)
        else:
            drops = weapons
    if drops:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            if i.id in drops
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    defender = defender.model_copy(
        update={
            "posture": "prone" if status.prone else defender.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == defender.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(
                    update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                )
                if a.actor_id == defender.actor_id and status.incapacitated
                else a
                for a in state.actors
            )
        }
    )
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                defender if p.actor_id == defender.actor_id else p for p in encounter.participants
            ),
            "blocked_reason": blocked,
        }
    )
    if blocked and blocked.startswith("basic-critical-miss:"):
        number = sum(critical_dice)
        subject = defender if blocked.endswith(":defender") else attacker
        affected_item = defense_item if subject.actor_id == defender.actor_id else pending.weapon_id
        if number in (7, 13):
            subject = subject.model_copy(update={"defense_penalty": -2})
            blocked = None
        elif number == 16:
            subject = subject.model_copy(update={"posture": "prone"})
            blocked = None
        elif number in (8, 9, 10, 11, 12) or (
            number == 14
            and (weapon.damage.basis != "swing" or subject.actor_id == defender.actor_id)
        ):
            from wayfarer.engine.simulation.combat.thrown.flight import position

            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={
                                        "ready": False,
                                        "equipped": False
                                        if number in (9, 10, 11, 14)
                                        else i.equipped,
                                        "ground": position(encounter, subject)
                                        if number in (9, 10, 11, 14)
                                        else i.ground,
                                    }
                                )
                                if i.id == affected_item
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
            subject = subject.model_copy(
                update={
                    "ready_item_ids": tuple(i for i in subject.ready_item_ids if i != affected_item)
                }
            )
            blocked = None
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    subject if p.actor_id == subject.actor_id else p for p in encounter.participants
                ),
                "blocked_reason": blocked,
            }
        )
    if blocked and sum(critical_dice) == 14 and not blocked.endswith(":defender"):
        from wayfarer.engine.simulation.combat.thrown.flight import resolve_flight

        state, encounter, flight_dice = resolve_flight(runtime, state, encounter, critical_dice)
        effect_dice += flight_dice
        updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
        status = updated_hp.injury
        assert status is not None
        injury = hp.current - updated_hp.current
        blocked = None
        encounter = encounter.model_copy(update={"blocked_reason": None})
    if blocked and blocked.startswith("basic-critical-miss:"):
        from wayfarer.engine.simulation.combat.critical import IncomingWound
        from wayfarer.engine.simulation.combat.criticals.context import capture_critical
        from wayfarer.engine.simulation.combat.objects.locations import from_behind

        incoming = (
            IncomingWound(
                actor_id=defender.actor_id,
                dice=dice_count,
                adds=adds,
                damage_type=weapon.damage.damage_type,
                resistance=resistance,
                ht=defend_build.statistics.ht,
                dx=defend_build.statistics.dx,
                hit_location=pending.hit_location,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
                from_behind=from_behind(attacker, defender),
                held_item_ids=held,
                hand_bindings=defender.hand_bindings,
                shield_item_ids=tuple(
                    i.id
                    for i in state.resources.items
                    if i.id in held and entries[i.definition_id].shield
                ),
            )
            if blocked.endswith(":defender")
            else None
        )
        state = capture_critical(
            runtime,
            state,
            encounter,
            tables=critical_tables or (critical_dice,),
            defender_item=defense_item,
            incoming=incoming,
            defender_mode_id=critical_parry_mode,
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        second_defense=second_trace,
        attack_value=attack_value,
        defense_value=defense_derived,
        damage_dice=dice,
        basic_damage=basic,
        resistance=resistance,
        injury=injury,
        hp_before=hp.current,
        hp_after=updated_hp.current,
        incapacitated=status.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_dice,
        adjudication_required=blocked,
        location=location,
        location_dice=location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
    )
    from wayfarer.engine.simulation.combat.maneuver_transitions import distracted

    encounter = distracted(
        runtime,
        state,
        encounter,
        defender.actor_id,
        defended=defense is not None,
        injured=injury > 0,
    )
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    if weapon.ready_after_attack:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False})
                            if i.id == pending.weapon_id
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
        actor = actor.model_copy(
            update={
                "ready_item_ids": tuple(i for i in actor.ready_item_ids if i != pending.weapon_id)
            }
        )
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    actor if p.actor_id == actor.actor_id else p for p in encounter.participants
                )
            }
        )
    from wayfarer.engine.simulation.combat.objects.locations import unavailable_hand

    attacker_status = next(
        p.injury for p in state.resources.pools if p.id == f"hp:{actor.actor_id}"
    )
    next_weapon = actor.maneuver_state.second_attack_item_id or pending.weapon_id
    attack_disabled = bool(
        attacker_status and (attacker_status.incapacitated or attacker_status.stunned)
    ) or any(
        unavailable_hand(disabled(state.resources, actor.actor_id), hand)
        for item_id, hand in actor.hand_bindings
        if item_id == next_weapon
    )
    if actor.maneuver_state.attacks_remaining and (
        attack_disabled
        or not any(i.id == next_weapon and i.equipped and i.ready for i in state.resources.items)
    ):
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={
                            "maneuver_state": p.maneuver_state.model_copy(
                                update={"attacks_remaining": 0}
                            )
                        }
                    )
                    if p.actor_id == actor.actor_id
                    else p
                    for p in encounter.participants
                )
            }
        )
    return state, encounter, trace
