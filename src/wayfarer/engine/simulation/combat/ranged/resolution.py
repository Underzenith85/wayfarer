"""Ranged dispatch in the encounter transaction (Lite 27-29; Basic B372-375).

Numeric baseline: Fourth Edition (2004). Exact-printing audit remains a
certification gate. No alternative inventory, injury or command receipt engine.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from wayfarer.engine.character.statistics import damage as strength_damage
from wayfarer.engine.rules.checks import Outcome, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty
from wayfarer.engine.rules.tables.ranged import (
    multiple_projectile_attack,
    range_penalty,
    rapid_fire_bonus,
)
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.rules.types.spray import Stream
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.entangle import attack_penalty as entangle_attack_penalty
from wayfarer.engine.simulation.combat.entangle import bind as entangle_bind
from wayfarer.engine.simulation.combat.firearm_transitions import (
    before_attack,
    roll_malfunction,
    set_failure,
)
from wayfarer.engine.simulation.combat.firearms import (
    MalfunctionRecord,
    save_malfunction,
    spend_rounds,
)
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.objects.combat import (
    damage_target,
    defense_stress,
    intercepted_projectiles,
    shield_damage,
    target_geometry,
    target_modifier,
)
from wayfarer.engine.simulation.combat.objects.locations import from_behind
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.ranged.ammunition import expend
from wayfarer.engine.simulation.combat.ranged.critical import RangedCritical, save_ranged_critical
from wayfarer.engine.simulation.combat.ranged.lingering_fire import _schedule_lingering_fire
from wayfarer.engine.simulation.combat.ranged.misses import resolve_miss
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.thrown.explosions import schedule_payload
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.unarmed.injury import critical_miss
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.hit_locations import (
    attack_penalty,
    disabled,
    location_special_effects,
    missing_location,
    part,
    select_location,
    torso_near_miss,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    selected: Defense,
    item_id: str | None,
    *,
    second_defense: Defense | None,
    second_item_id: str | None,
    parry_mode_id: str | None = None,
    second_parry_mode_id: str | None = None,
    catch_thrown: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]:

    original_resources = state.resources
    pending = encounter.pending_defense
    assert pending is not None
    if selected not in pending.allowed or (
        second_defense and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense cannot stop this projectile")
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    original_target = target
    geometry = encounter
    if pending.target_item_id:
        geometry = target_geometry(runtime, state, encounter, pending.target_item_id)
    scene = situation(
        runtime,
        geometry,
        actor.actor_id,
        target.actor_id,
        weapon,
        ground=bool(
            pending.target_item_id
            and next(i for i in state.resources.items if i.id == pending.target_item_id).ground
        ),
    )
    equipment = catalog(runtime)
    compiled = build(runtime, state, actor.actor_id)
    defender_build = build(runtime, state, target.actor_id)
    stats = compiled.statistics
    defender_stats = defender_build.statistics
    assert stats is not None and defender_stats is not None
    value = level(compiled, weapon.skill_id)
    actor_hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    validate_rated_strength(equipment.profile_id, weapon, st)
    aim = actor.maneuver_state
    aimed = (
        (aim.aim_item_id, aim.aim_mode_id, aim.aim_target_id)
        == (
            pending.weapon_id,
            weapon.id,
            target.actor_id,
        )
        and aim.aim_seconds > 0
        and not pending.vehicle_aim_lost
    )
    bonus = (
        pending.suppression_aim_bonus
        if pending.suppression_zone_id is not None
        else aim.aim_bonus
        if aimed
        else 0
    )
    if pending.suppression_zone_id is None:
        if actor.last_maneuver == "move_and_attack":
            bonus = min(-2, weapon.bulk)
        elif actor.last_maneuver == "all_out_attack":
            bonus += 1
    effective_shots = pending.shots
    close_projectile_multiplier = 1
    if weapon.multiple_projectiles is not None:
        assert weapon.half_damage_range is not None
        effective_shots, close_projectile_multiplier = multiple_projectile_attack(
            pending.shots,
            weapon.multiple_projectiles.projectiles_per_shot,
            scene.distance,
            float(weapon.half_damage_range),
        )
    attack_target = (
        int(value.value)
        + bonus
        + scene.size_modifier
        + range_penalty(scene.distance + scene.speed_yards_per_second)
        + rapid_fire_bonus(effective_shots)
        # A mount bears the weapon, so the firer's own ST is not what limits it.
        - (0 if weapon.mount is not None else minimum_strength_penalty(weapon.minimum_st, st))
        + pending.vehicle_attack_penalty
    )
    if pending.laser_sight and scene.laser_visible_to_firer:
        attack_target += 1
    if pending.target_item_id:
        attack_target += (
            target_modifier(runtime, state, target.actor_id, pending.target_item_id)
            - scene.size_modifier
        )
    attack_target += entangle_attack_penalty(actor)
    attack_target -= actor_hp.injury.shock if actor_hp.injury else 0
    if actor_hp.injury and pending.suppression_zone_id is None:
        attack_target += actor_hp.injury.physical_traits.darkness(encounter.darkness_penalty)

    eyes = disabled(state.resources, actor.actor_id) & {"left-eye", "right-eye"}
    if eyes:
        attack_target -= (
            6 if len(eyes) == 2 else 1 if aimed and actor.last_maneuver != "move_and_attack" else 3
        )
    if pending.hit_location:
        entries = {e.definition_id: e for e in equipment.entries}
        shield_side = next(
            (
                hand.split("-")[0]
                for item, hand in target.hand_bindings
                if any(
                    i.id == item and entries[i.definition_id].shield for i in state.resources.items
                )
            ),
            None,
        )
        attack_target += attack_penalty(pending.hit_location, shield_side=shield_side)
    if pending.suppression_skill_cap is not None:
        attack_target = min(
            attack_target,
            pending.suppression_skill_cap + rapid_fire_bonus(effective_shots),
        )
    defense_value_, defense_item = defense_value(
        runtime, state, target, selected, item_id, parry_mode_id=parry_mode_id
    )
    second_value, second_item = defense_value(
        runtime,
        state,
        target,
        second_defense or "none",
        second_item_id,
        parry_mode_id=second_parry_mode_id,
    )
    if pending.laser_sight and scene.laser_visible_to_target:
        if selected == "dodge" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value + 1, ())
        if second_defense == "dodge" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value + 1, ())
    if weapon.thrown:
        thrown_item = next(i for i in state.resources.items if i.id == pending.weapon_id)
        entry = next(e for e in equipment.entries if e.definition_id == thrown_item.definition_id)
        penalty = 2 if entry.weight_millipounds <= 1000 else 1
        if selected == "parry" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value - penalty, ())
        if second_defense == "parry" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value - penalty, ())

    state = state.model_copy(
        update={"resources": before_attack(state.resources, pending.weapon_id, weapon)}
    )
    attack = success_roll(
        equipment.profile_id,
        attack_target,
        check_modifiers(state.resources, actor.actor_id, "dx"),
        rng=runtime.rng,
    )
    original_attack = attack
    attack, shots_fired, malfunction_table, failure = roll_malfunction(
        runtime,
        weapon,
        attack,
        cause_id=pending.id,
        shots=pending.shots,
        rapid_bonus=rapid_fire_bonus(effective_shots),
    )
    if failure is not None:
        state = state.model_copy(
            update={"resources": set_failure(state.resources, pending.weapon_id, failure)}
        )
    # B382 excludes ranged attacks from the generic failure-by-ten rule.
    if equipment.profile_id == "gurps-basic-set-4e-2004":
        attack = replace(attack, rule_id="gurps.combat.ranged_attack")
        if attack.outcome is Outcome.CRITICAL_FAILURE and attack.total < 17:
            attack = replace(attack, outcome=Outcome.FAILURE)

    near_miss = bool(shots_fired) and torso_near_miss(pending.hit_location, attack)
    effective_shots_fired = (
        shots_fired
        if weapon.multiple_projectiles is None or close_projectile_multiplier > 1
        else shots_fired * weapon.multiple_projectiles.projectiles_per_shot
    )
    hits = (
        min(
            effective_shots_fired,
            1
            + max(0, attack.effective_target - sum(attack.dice))
            // (weapon.recoil + pending.spray_recoil_penalty),
        )
        if attack.outcome.succeeded
        else int(near_miss)
    )
    if pending.suppression_zone_id is not None:
        hits = min(hits, pending.suppression_remaining_hits)
    initial_hits = hits
    defense = None
    second_trace = None
    if hits and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_value_ is not None:
        defense = success_roll(equipment.profile_id, int(defense_value_.value), rng=runtime.rng)
        if defense.outcome.succeeded:
            avoided = (
                hits
                if defense.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(defense_value_.value) - sum(defense.dice) if selected == "dodge" else 1
                )
            )
            hits = max(0, hits - avoided)
        if selected == "parry" and defense_item is not None:
            target = target.model_copy(update={"parries": target.parries + (defense_item,)})
        if selected == "block":
            target = target.model_copy(update={"block_used": True})
        if defense.outcome is Outcome.CRITICAL_FAILURE and selected == "dodge":
            target = target.model_copy(update={"posture": "prone"})
    if hits and defense is not None and not defense.outcome.succeeded and second_value is not None:
        state, encounter = defense_stress(
            runtime,
            state,
            encounter,
            target.actor_id,
            pending.id + ":second",
            second_item,
        )
        target = target.model_copy(
            update={
                "ready_item_ids": tuple(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == target.actor_id and i.equipped and i.ready
                )
            }
        )
        try:
            second_value, second_item = defense_value(
                runtime,
                state,
                target,
                second_defense or "none",
                second_item,
                parry_mode_id=second_parry_mode_id,
            )
            if weapon.thrown and second_defense == "parry" and second_value:
                second_value = DerivedValue(second_value.target, second_value.value - penalty, ())
        except ValidationError:
            second_value = None
    if hits and defense is not None and not defense.outcome.succeeded and second_value is not None:
        second_trace = success_roll(equipment.profile_id, int(second_value.value), rng=runtime.rng)
        if second_trace.outcome.succeeded:
            avoided = (
                hits
                if second_trace.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(second_value.value) - sum(second_trace.dice)
                    if second_defense == "dodge"
                    else 1
                )
            )
            hits = max(0, hits - avoided)
        if second_defense == "parry" and second_item is not None:
            target = target.model_copy(update={"parries": target.parries + (second_item,)})
        if second_defense == "block":
            target = target.model_copy(update={"block_used": True})
        if second_trace.outcome is Outcome.CRITICAL_FAILURE and second_defense == "dodge":
            target = target.model_copy(update={"posture": "prone"})

    shield_hit, shield_impacts = intercepted_projectiles(
        runtime,
        state,
        encounter,
        second_trace or defense,
        second_defense if second_trace else selected,
        initial_hits,
    )
    impacts = hits + shield_impacts
    dropped = {
        equipment_id
        for choice, roll, equipment_id in (
            (selected, defense, defense_item),
            (second_defense, second_trace, second_item),
        )
        if choice == "block"
        and roll is not None
        and roll.outcome is Outcome.CRITICAL_FAILURE
        and equipment_id is not None
    }
    if dropped:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False}) if i.id in dropped else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    critical_table = (
        draw_dice(runtime.rng, 3)
        if equipment.profile_id == "gurps-basic-set-4e-2004"
        and attack.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS)
        else ()
    )
    critical = sum(critical_table) if attack.outcome is Outcome.CRITICAL_SUCCESS else 0
    blocked = "ranged-critical-table" if critical_table and not critical else None
    critical_parry = next(
        (
            (equipment_id, selected_mode)
            for choice, roll, equipment_id, selected_mode in (
                (selected, defense, defense_item, parry_mode_id),
                (second_defense, second_trace, second_item, second_parry_mode_id),
            )
            if choice == "parry" and roll is not None and roll.outcome is Outcome.CRITICAL_FAILURE
        ),
        None,
    )
    parry_item, critical_parry_mode = critical_parry or (None, None)
    if parry_item is not None:
        critical_table = draw_dice(runtime.rng, 3)
        blocked = "ranged-critical-parry"
    critical_rolls: tuple[tuple[int, int, int], ...] = (
        (cast(tuple[int, int, int], critical_table),) if critical_table else ()
    )
    miss_effect_dice: tuple[int, ...] = ()
    miss_lasting_ids: tuple[str, ...] = ()
    if blocked and parry_item in ("left-hand", "right-hand"):
        encounter = CombatEngine._replace(encounter, target)
        state, encounter, checks, dice, handled = critical_miss(
            runtime,
            state,
            encounter,
            PendingUnarmed(
                id=pending.id,
                actor_id=pending.attacker_id,
                target_id=pending.defender_id,
                action="punch",
                skill="attribute:dx",
                hands=(),
                allowed=("none", "parry"),
            ),
            target.actor_id,
            critical_table,
            parry_item,
        )
        miss_effect_dice = dice + tuple(d for check in checks for d in check.dice)
        blocked = None if handled else "ranged-critical-unarmed-parry"
        target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    elif blocked:
        # Preserve defense counters/posture before applying consequences to the defender.
        encounter = CombatEngine._replace(encounter, target)
        state, encounter, miss, blocked = resolve_miss(
            runtime,
            state,
            encounter,
            critical_table,
            parry_item=parry_item,
            parry_mode_id=critical_parry_mode,
        )
        critical_rolls = miss.table_rolls
        critical_table = miss.table_rolls[-1]
        miss_effect_dice = miss.location_dice + miss.damage_dice
        miss_lasting_ids = miss.lasting_injury_ids
        if parry_item is not None:
            target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    caught_hand = next(
        (
            equipment_id
            for choice, roll, equipment_id in (
                (selected, defense, defense_item),
                (second_defense, second_trace, second_item),
            )
            if catch_thrown
            and choice == "parry"
            and equipment_id in ("left-hand", "right-hand")
            and roll is not None
            and roll.outcome is Outcome.CRITICAL_SUCCESS
        ),
        None,
    )
    if failure is not None and pending.spray_targets:
        encounter = encounter.model_copy(
            update={"pending_defense": pending.model_copy(update={"spray_targets": ()})}
        )
    encounter = CombatEngine._replace(encounter, target)
    if pending.suppression_zone_id is None:
        state, encounter = expend(
            runtime,
            state,
            encounter,
            weapon,
            shots=(shots_fired + pending.traversal_shots)
            if weapon.sprayer is None
            else weapon.sprayer.rounds_per_second,
            hit=bool(hits),
            catcher_id=target.actor_id if caught_hand else None,
            hand=caught_hand,
        )
    target = next(p for p in encounter.participants if p.actor_id == target.actor_id)

    state, encounter, payload_attack = schedule_payload(
        runtime,
        state,
        encounter,
        weapon,
        original_resources=original_resources,
        failure=failure,
        hits=hits,
        shots_fired=shots_fired,
        critical=critical,
    )
    target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    if payload_attack:
        hits = 0
        impacts = shield_impacts
    if pending.suppression_zone_id is not None:
        encounter = encounter.model_copy(
            update={
                "suppression_zones": tuple(
                    zone.model_copy(
                        update={"remaining_hits": max(0, zone.remaining_hits - impacts)}
                    )
                    if zone.id == pending.suppression_zone_id
                    else zone
                    for zone in encounter.suppression_zones
                    if not (
                        failure is not None
                        and zone.attacker_id == pending.attacker_id
                        and zone.weapon_id == pending.weapon_id
                    )
                )
            }
        )
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    if hits and pending.hit_location:
        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=runtime.rng,
            from_behind=from_behind(actor, target),
        )
        if pending.hit_location == "random" and hp.injury and missing_location(hp.injury, location):
            location = "torso"
    # B373/B556: a burst rolls the critical table once. The critical projectile may
    # be redirected (eye); the remaining projectiles keep the declared location.
    base_location, base_location_dice = location, location_dice
    head = (
        location in ("skull", "face", "left-eye", "right-eye")
        and weapon.damage.damage_type != "tox"
        and hp.injury is not None
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    lasting_ids: tuple[str, ...] = miss_lasting_ids
    effect_dice: tuple[int, ...] = miss_effect_dice
    if critical and head and blocked is None:
        if critical in (6, 7) and location in ("face", "skull"):
            if from_behind(actor, target) or (
                hp.injury and hp.injury.tolerance and hp.injury.tolerance.no_eyes
            ):
                critical = 4
            else:
                eye_die = draw_dice(runtime.rng, 1)[0]
                effect_dice += (eye_die,)
                location = "right-eye" if eye_die <= 3 else "left-eye"
                critical_eye = True
        if critical == 8:
            target = target.model_copy(update={"forced_do_nothing": True})
    damages: list[int] = []
    injuries: list[int] = []
    damage_dice: list[int] = []
    entries = {e.definition_id: e for e in equipment.entries}

    def armor_dr() -> int:
        return max(
            (
                armor.dr
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.equipped
                and (i.condition is None or not i.condition.disabled)
                for armor in (entries[i.definition_id].armor,)
                if armor is not None
                and (
                    (location or "torso") in armor.locations
                    or (part(location) + "s" if location else "torso") in armor.locations
                )
            ),
            default=0,
        )

    dr_bonus = 0

    if runtime.rules.abilities is not None:
        dr_bonus = damage_resistance(
            state.resources, target.actor_id, build_revision=defender_build.revision
        )
    environmental_dr = scene.beam_environment_dr if weapon.beam_environment_dr else 0

    vehicle_cover = max(
        (
            transport.occupant_cover_dr
            for transport in state.resources.transports
            if target.actor_id in transport.occupants
            and encounter.spatial_kind == "hex"
            and isinstance(target.position, Hex)
            and target.position == Hex(q=transport.q, r=transport.r)
            and target.hex_facing == transport.facing
        ),
        default=0,
    )
    dr = (armor_dr() + dr_bonus + environmental_dr + vehicle_cover) * close_projectile_multiplier
    first_location, first_location_dice, first_dr = location, location_dice, dr
    hit_resistances: list[int] = []
    hit_locations: list[HumanLocation | None] = []
    hit_location_dice: list[tuple[int, ...]] = []
    expression = stats.swing if weapon.damage.basis == "swing" else stats.thrust
    range_st = st
    if weapon.rated_strength is not None:
        range_st = weapon.rated_strength.st
        expression = strength_damage(equipment.profile_id, range_st)[0]
    count = (weapon.damage.dice or expression.dice) * close_projectile_multiplier
    adds = (
        weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    ) * close_projectile_multiplier
    half = weapon.half_damage_range is not None and scene.distance >= float(
        weapon.half_damage_range
    ) * (range_st if weapon.range_basis == "st" else 1)
    resistance_damage = (
        weapon.damage
        if close_projectile_multiplier == 1
        else weapon.damage.model_copy(
            update={
                "armor_divisor": weapon.damage.armor_divisor / close_projectile_multiplier,
            }
        )
    )
    resistance_weapon = (
        weapon
        if close_projectile_multiplier == 1
        else weapon.model_copy(update={"damage": resistance_damage})
    )
    for index in range(impacts if blocked is None else 0):
        if index and pending.hit_location == "random":
            location, location_dice = select_location(
                "random",
                rng=runtime.rng,
                from_behind=from_behind(actor, target),
            )
            current_hp = next(p for p in state.resources.pools if p.id == hp.id)
            if current_hp.injury and missing_location(current_hp.injury, location):
                location = "torso"
            dr = (armor_dr() + dr_bonus + vehicle_cover) * close_projectile_multiplier
        elif index and location != base_location:
            location, location_dice = base_location, base_location_dice
            dr = (armor_dr() + dr_bonus + vehicle_cover) * close_projectile_multiplier
        hit_resistances.append(dr)
        hit_locations.append(location)
        hit_location_dice.append(location_dice)
        hit_critical = critical if index == 0 else 0
        maximum = hit_critical in ((3, 15) if head else (6, 15)) or (
            equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
        )
        dice = () if maximum else draw_dice(runtime.rng, count)
        damage_dice.extend(dice)
        damage = max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * count if maximum else sum(dice)) + adds,
        )
        damage *= (
            3
            if hit_critical in ((18,) if head else (3, 18))
            else 2
            if hit_critical in ((16,) if head else (5, 16))
            else 1
        )
        if half:
            damage //= 2
        acceleration = weapon.rocket_acceleration
        if acceleration is not None:
            damage //= (
                acceleration.close_damage_divisor
                if scene.distance <= acceleration.close_max_yards
                else acceleration.medium_damage_divisor
                if scene.distance <= acceleration.medium_max_yards
                else 1
            )

        if pending.target_item_id:
            state, encounter, object_result = damage_target(
                runtime,
                state,
                encounter,
                pending.target_item_id,
                damage,
                resistance_damage,
                impact=index,
            )
            damages.append(damage)
            injuries.append(0)
            hit_resistances[-1] = object_result.effective_dr if object_result else 0
            if object_result:
                effect_dice += tuple(d for roll in object_result.checks for d in roll)
            continue
        if shield_hit and index < shield_impacts:
            state, encounter, damage = shield_damage(
                runtime,
                state,
                encounter,
                shield_hit,
                damage,
                resistance_weapon,
                impact=index,
            )
            if damage == 0:
                damages.append(0)
                injuries.append(0)
                continue
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=f"{pending.id}:hit:{index}",
                actor_id=target.actor_id,
                expected_revision=state.resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type=weapon.damage.damage_type,
                location=location,
                critical_eye=critical_eye and index == 0,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
            ),
            ht=defender_stats.ht,
            rng=runtime.rng,
            system=True,
            dx=defender_stats.dx,
            force_major_wound=hit_critical in ((4, 5) if head else (7, 13, 14)),
            double_shock=hit_critical == 8 and not head,
            funny_bone=hit_critical == 8 and not head,
            halve_dr=("up" if head else "down")
            if hit_critical in ((4, 5, 17) if head else (4, 17))
            else None,
            ignore_dr=head and hit_critical == 3,
            held_item_locations=target.hand_bindings,
            shield_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.ready
                and i.equipped
                and entries[i.definition_id].shield
            ),
            held_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id and i.ready and i.equipped
            ),
            head_trauma=(
                "deafened"
                if head and hit_critical in (12, 13) and weapon.damage.damage_type == "cr"
                else "scarred"
                if head and hit_critical in (12, 13)
                else None
            ),
            scar_levels=2 if weapon.damage.damage_type in ("burn", "cor") else 1,
        )
        state = state.model_copy(update={"resources": resources})
        damages.append(damage)
        injuries.append(result.injury)
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
    if critical == 12 and not head and blocked is None and not pending.target_item_id:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "ready": False,
                                    "equipped": False,
                                    "container_id": None,
                                    "ground": position(encounter, target),
                                }
                            )
                            if i.owner_id == target.actor_id and i.ready
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    if head and critical == 14 and blocked is None and not pending.target_item_id:
        held_weapons = tuple(
            i.id
            for i in state.resources.items
            if i.owner_id == target.actor_id
            and i.ready
            and i.equipped
            and entries[i.definition_id].modes
        )
        if held_weapons:
            die = draw_dice(runtime.rng, 1)[0] if len(held_weapons) > 1 else None
            if die is not None:
                effect_dice += (die,)
            drop = held_weapons[0 if die is None or die <= 3 else 1]
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={
                                        "ready": False,
                                        "equipped": False,
                                        "container_id": None,
                                        "ground": position(encounter, target),
                                    }
                                )
                                if i.id == drop
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
    if weapon.sprayer is not None:
        state = _schedule_lingering_fire(
            runtime,
            state,
            encounter,
            weapon,
            pending=pending,
            target_id=target.actor_id,
            basic_damage=max(damages, default=0),
            hit=bool(hits and pending.target_item_id is None),
        )
        # Hold the stream for this second, walking it to whichever target the
        # firer laid it on. Every second is paid for and rolled separately.
        held = actor.stream
        opened = (
            held.sustain(target.actor_id)
            if held is not None and (held.weapon_id, held.mode_id) == (pending.weapon_id, weapon.id)
            else Stream(
                weapon_id=pending.weapon_id,
                mode_id=weapon.id,
                target_id=target.actor_id,
                seconds=1,
                sustained_seconds=weapon.sprayer.sustained_seconds,
                ignites=weapon.sprayer.ignites,
            )
        )
        encounter = CombatEngine._replace(encounter, actor.model_copy(update={"stream": opened}))
    updated = next(p for p in state.resources.pools if p.id == hp.id)
    assert updated.injury is not None
    # B181/B211: a landed binding holds the target whatever damage it also did.
    # A defended-away or blocked shot binds nothing.
    if weapon.entangle is not None and hits and blocked is None:
        target = entangle_bind(
            target,
            weapon.entangle,
            source_actor_id=actor.actor_id,
            weapon_definition_id=next(
                i.definition_id
                for i in (*state.resources.items, *state.resources.expended_items)
                if i.id == pending.weapon_id
            ),
            mode_id=weapon.id,
        )
    target = target.model_copy(
        update={
            "posture": "prone" if updated.injury.prone else target.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == target.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target).model_copy(
        update={"blocked_reason": blocked}
    )
    encounter = distracted(
        runtime,
        state,
        encounter,
        target.actor_id,
        defended=defense is not None,
        injured=sum(injuries) > 0,
    )
    if updated.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == target.actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        second_defense=second_trace,
        attack_value=value,
        defense_value=defense_value_,
        damage_dice=tuple(damage_dice),
        basic_damage=sum(damages),
        resistance=first_dr,
        injury=sum(injuries),
        hp_before=hp.current,
        hp_after=updated.current,
        incapacitated=updated.injury.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_table,
        location=first_location,
        location_dice=first_location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
        adjudication_required=blocked,
        shots_fired=shots_fired,
        malfunction_table=malfunction_table,
        malfunction=failure.kind if failure is not None else None,
        hits=impacts,
        per_hit_damage=tuple(damages),
        per_hit_injury=tuple(injuries),
        per_hit_resistance=tuple(hit_resistances) if effective_shots > 1 else (),
        per_hit_locations=tuple(hit_locations) if effective_shots > 1 else (),
        per_hit_location_dice=tuple(hit_location_dice) if effective_shots > 1 else (),
    )
    if failure is not None:
        state = state.model_copy(
            update={
                "resources": save_malfunction(
                    state.resources,
                    MalfunctionRecord(
                        id=pending.id,
                        encounter_id=encounter.id,
                        attacker=actor,
                        defender=original_target,
                        attacker_build_revision=compiled.revision,
                        defender_build_revision=defender_build.revision,
                        catalog=equipment,
                        weapon=weapon,
                        scene=scene,
                        original_attack=original_attack,
                        ammunition_load=next(
                            (
                                v
                                for v in original_resources.ammunition_loads
                                if v.weapon_id == pending.weapon_id
                            ),
                            None,
                        ),
                        items=tuple(
                            i
                            for i in original_resources.items
                            if i.owner_id in (actor.actor_id, target.actor_id)
                        ),
                        pools=tuple(
                            p
                            for p in original_resources.pools
                            if p.id
                            in (
                                f"hp:{actor.actor_id}",
                                f"fp:{actor.actor_id}",
                                f"hp:{target.actor_id}",
                                f"fp:{target.actor_id}",
                            )
                        ),
                        failure=failure,
                        trace=trace,
                    ),
                )
            }
        )
    if (
        weapon.firearm
        and weapon.firearm.action == "single-use"
        and (shots_fired or failure and failure.kind == "dud")
    ):
        spent_resources = state.resources
        if failure and failure.kind == "dud":
            spent_resources = spend_rounds(spent_resources, pending.weapon_id, 1)
        spent = next(i for i in spent_resources.items if i.id == pending.weapon_id)
        state = state.model_copy(
            update={
                "resources": spent_resources.model_copy(
                    update={
                        "items": tuple(i for i in spent_resources.items if i.id != spent.id),
                        "expended_items": spent_resources.expended_items
                        + (
                            spent.model_copy(
                                update={"ready": False, "equipped": False, "container_id": None}
                            ),
                        ),
                    }
                )
            }
        )
    if critical_table:
        state = state.model_copy(
            update={
                "resources": save_ranged_critical(
                    state.resources,
                    RangedCritical(
                        id=pending.id,
                        encounter_id=encounter.id,
                        created_at=state.resources.game_time,
                        attacker=actor,
                        defender=original_target,
                        attacker_build_revision=compiled.revision,
                        defender_build_revision=defender_build.revision,
                        catalog=equipment,
                        weapon=weapon,
                        scene=scene,
                        ammunition_load=next(
                            (
                                load
                                for load in original_resources.ammunition_loads
                                if load.weapon_id == pending.weapon_id
                            ),
                            None,
                        ),
                        items=tuple(
                            i
                            for i in original_resources.items
                            if i.owner_id in (actor.actor_id, target.actor_id)
                        ),
                        pools=tuple(
                            p
                            for p in original_resources.pools
                            if p.id
                            in (
                                f"hp:{actor.actor_id}",
                                f"fp:{actor.actor_id}",
                                f"hp:{target.actor_id}",
                                f"fp:{target.actor_id}",
                            )
                        ),
                        trace=trace,
                        table_rolls=critical_rolls,
                        subject_id=target.actor_id if parry_item else actor.actor_id,
                        affected_item_id=parry_item or pending.weapon_id,
                        affected_mode_id=critical_parry_mode if parry_item else weapon.id,
                    ),
                )
            }
        )
    return state, encounter, trace
