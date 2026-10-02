"""Ranged dispatch in the encounter transaction (Lite 27-29; Basic B372-375).

Numeric baseline: Fourth Edition (2004). Exact-printing audit remains a
certification gate. No alternative inventory, injury or command receipt engine.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING, Literal, cast, overload

from wayfarer.engine.character.statistics import damage as strength_damage
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.character.traits.mastery import damage_bonus
from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty
from wayfarer.engine.rules.tables.ranged import (
    multiple_projectile_attack,
    range_penalty,
    rapid_fire_bonus,
)
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.rules.types.spray import Stream
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.entangle import attack_penalty as entangle_attack_penalty
from wayfarer.engine.simulation.combat.entangle import bind as entangle_bind
from wayfarer.engine.simulation.combat.equipment_effects import defense_stress
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
    intercepted_projectiles,
    shield_damage,
    target_geometry,
    target_modifier,
)
from wayfarer.engine.simulation.combat.objects.locations import from_behind, item_hands
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.ranged.ammunition import expend
from wayfarer.engine.simulation.combat.ranged.critical import RangedCritical, save_ranged_critical
from wayfarer.engine.simulation.combat.ranged.damage_records import (
    PreparedRangedDamage,
    RangedDamageContext,
    RangedDamageProgress,
    RangedDamageStage,
)
from wayfarer.engine.simulation.combat.ranged.equipment import (
    ammunition_profile,
    effective_mode,
    persist_back_blast,
    persist_follow_up,
    persist_surge,
    resolve_follow_up,
)
from wayfarer.engine.simulation.combat.ranged.gunslinger import accuracy_bonus
from wayfarer.engine.simulation.combat.ranged.lingering_fire import _schedule_lingering_fire
from wayfarer.engine.simulation.combat.ranged.misses import resolve_miss
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.ranged.special import impact_cover, living_cover_dr
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.special_melee import targeted_attack_penalty
from wayfarer.engine.simulation.combat.thrown.explosions import schedule_payload, separation
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.unarmed.injury import critical_miss
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed
from wayfarer.engine.simulation.combat.visibility import external_defense_penalty, optical_bonus
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.engine.simulation.equipment.silver import (
    attack_construction,
    silver_wounding_multiplier,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.hit_locations import (
    disabled,
    location_special_effects,
    missing_location,
    part,
    select_location,
    torso_near_miss,
)
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.traits.size_forms import size_delta
from wayfarer.errors import ValidationError


def _visibility_adjustment(value: DerivedValue | None, penalty: int) -> DerivedValue | None:
    if value is None:
        return None
    return DerivedValue(value.target, value.value + penalty, value.explanations)


def _laser_defense(
    value: DerivedValue | None, selected: Defense | None, visible: bool
) -> DerivedValue | None:
    """B412: seeing a laser gives Dodge its source-specific warning bonus."""
    if visible and selected == "dodge" and value is not None:
        return DerivedValue(value.target, value.value + 1, ())
    return value


def _apply_one_handed_readiness(
    state: PlayState,
    actor_id: str,
    weapon_item_id: str,
    weapon: RangedMode,
    st: int,
) -> PlayState:
    if not (
        weapon.one_handed_unready_st_multiplier is not None
        and len(item_hands(state, actor_id, weapon_item_id)) == 1
        and Decimal(st) < Decimal(weapon.minimum_st) * weapon.one_handed_unready_st_multiplier
    ):
        return state
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"ready": False}) if item.id == weapon_item_id else item
                for item in state.resources.items
            )
        }
    )
    return state.model_copy(update={"resources": resources})


def _resolve_follow_up_hit(
    runtime: RulesContext,
    state: PlayState,
    *,
    event_id: str,
    target_actor_id: str,
    target_ht: int,
    resistance_dr: int,
    injury: int,
    spec: FollowUpSpec | None,
) -> tuple[PlayState, tuple[int, ...]]:
    if spec is None:
        return state, ()
    dice: tuple[int, ...] = (
        () if spec.requires_penetration and injury <= 0 else draw_dice(runtime.rng, 3)
    )
    roll = cast(tuple[int, int, int], dice if dice else (0, 0, 0))
    result = resolve_follow_up(
        spec,
        penetrated_damage=injury,
        target_ht=target_ht,
        resistance_dice=roll,
        resistance_dr=resistance_dr,
    )
    resources = persist_follow_up(
        state.resources,
        event_id=event_id,
        target_actor_id=target_actor_id,
        spec=spec,
        result=result,
    )
    return state.model_copy(update={"resources": resources}), dice


def _resolve_overpenetration(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    secondary_id: str | None,
    projectile_id: str,
    rolled_damage: int,
    intervening_dr: int,
    primary_hp: int,
    primary_armor_dr: int,
    blocked_by_shield: bool,
    location: HumanLocation | None,
) -> tuple[PlayState, Encounter]:
    """Apply the original projectile to one declared target behind living cover."""

    cover_threshold = intervening_dr + living_cover_dr(
        hp=primary_hp,
        armor_dr=primary_armor_dr,
        armor_divisor=weapon.damage.armor_divisor,
    )
    if secondary_id is None or blocked_by_shield or rolled_damage <= cover_threshold:
        return state, encounter
    secondary = next(p for p in encounter.participants if p.actor_id == secondary_id)
    secondary_build = build(runtime, state, secondary_id)
    secondary_stats = secondary_build.statistics
    assert secondary_stats is not None
    secondary_hp = next(p for p in state.resources.pools if p.id == f"hp:{secondary_id}")
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    secondary_armor = max(
        (
            armor.dr
            for item in state.resources.items
            if item.owner_id == secondary_id
            and item.equipped
            and (item.condition is None or not item.condition.disabled)
            for armor in (entries[item.definition_id].armor,)
            if armor is not None
            and (
                (location or "torso") in armor.locations
                or (part(location) + "s" if location else "torso") in armor.locations
            )
        ),
        default=0,
    )
    if runtime.rules.abilities is not None:
        secondary_armor += damage_resistance(
            state.resources, secondary_id, build_revision=secondary_build.revision
        )
    secondary_effective = int(
        (Decimal(secondary_armor) / weapon.damage.armor_divisor).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id=f"{projectile_id}:overpenetration:{secondary_id}",
            actor_id=secondary_id,
            expected_revision=state.resources.revision,
            basic_damage=rolled_damage,
            resistance=cover_threshold + secondary_effective,
            damage_type=weapon.damage.damage_type,
            location=location,
            tight_beam=weapon.damage.tight_beam,
        ),
        ht=secondary_stats.ht,
        dx=secondary_stats.dx,
        rng=runtime.rng,
        system=True,
    )
    updated_state = state.model_copy(update={"resources": resources})
    updated_secondary = next(p for p in resources.pools if p.id == secondary_hp.id)
    updated_encounter = CombatEngine._replace(
        encounter,
        secondary.model_copy(
            update={
                "posture": "prone"
                if updated_secondary.injury and updated_secondary.injury.prone
                else secondary.posture,
                "ready_item_ids": tuple(
                    sorted(
                        item.id
                        for item in resources.items
                        if item.owner_id == secondary_id and item.ready and item.equipped
                    )
                ),
            }
        ),
    )
    return updated_state, updated_encounter


def _resolve_cover_impact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    damage: Damage,
    *,
    cover_item_id: str | None,
    projectile_id: str,
    target_id: str,
    basic_damage: int,
) -> tuple[PlayState, Encounter, int]:
    """Apply an optional physical barrier and return its effective DR."""

    if cover_item_id is None:
        return state, encounter, 0
    updated_state, updated_encounter, cover = impact_cover(
        runtime,
        state,
        encounter,
        projectile_id=projectile_id,
        barrier_item_id=cover_item_id,
        target_id=target_id,
        basic_damage=basic_damage,
        damage=damage,
    )
    return updated_state, updated_encounter, cover.cover_dr


if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def _shield_side_effect(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    target_actor_id: str,
    shield_id: str,
    resistance: int,
    effect_dice: tuple[int, ...],
    hit_locations: list[HumanLocation | None],
    hit_resistances: list[int],
) -> tuple[int, ...]:
    side_die = draw_dice(runtime.rng, 1)[0]
    original = next(
        p
        for saved in state.encounters
        if saved.id == encounter.id
        for p in saved.participants
        if p.actor_id == target_actor_id
    )
    hand = next((hand for item, hand in original.hand_bindings if item == shield_id), None)
    if side_die <= 2 and hand:
        hit_locations[-1] = "left-arm" if hand == "left-hand" else "right-arm"
        hit_resistances[-1] = resistance
    return effect_dice + (side_die,)


def _maneuver_bonus(actor: Combatant, weapon: RangedMode, bonus: int) -> int:
    if actor.last_maneuver == "move_and_attack":
        return min(-2, weapon.bulk)
    if actor.last_maneuver == "all_out_attack":
        return bonus + 1
    return bonus


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[False] = False,
    prepare_damage: Literal[False] = False,
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]: ...


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[True],
    prepare_damage: Literal[False] = False,
    secret_damage: bool = False,
) -> AttackRollSpec: ...


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[False] = False,
    prepare_damage: Literal[True],
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace] | RangedDamageStage: ...


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
    selected_attack: CheckTrace | None = None,
    prepare_only: bool = False,
    prepare_damage: bool = False,
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace] | AttackRollSpec | RangedDamageStage:
    original_resources = state.resources
    pending = encounter.pending_defense
    assert pending is not None
    equipment = catalog(runtime)
    entries = {entry.definition_id: entry for entry in equipment.entries}
    loaded_ammunition = ammunition_profile(equipment, state.resources, pending.weapon_id, weapon)
    weapon = effective_mode(weapon, loaded_ammunition)
    follow_up = weapon.linked_follow_up or (
        loaded_ammunition.follow_up if loaded_ammunition is not None else None
    )
    if selected not in pending.allowed or (
        second_defense and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense cannot stop this projectile")
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    aim_target_id = pending.protected_defender_id or pending.defender_id
    original_target = target
    geometry = encounter
    if pending.target_item_id:
        geometry = target_geometry(runtime, state, encounter, pending.target_item_id)
    scene = situation(
        runtime,
        geometry,
        actor.actor_id,
        aim_target_id,
        weapon,
        ground=bool(
            pending.target_item_id
            and next(i for i in state.resources.items if i.id == pending.target_item_id).ground
        ),
    )
    attack_distance = (
        float(separation(position(encounter, actor), pending.area_aim_point))
        if pending.area_aim_point is not None
        else scene.distance
    )
    compiled = build(runtime, state, actor.actor_id)
    defender_build = build(runtime, state, target.actor_id)
    stats = compiled.statistics
    defender_stats = defender_build.statistics
    assert stats is not None and defender_stats is not None
    construction = attack_construction(original_resources, pending.weapon_id, weapon)
    target_traits = attack_defense_traits(defender_build, runtime.reviewer.compiler.definitions)
    vulnerability_multiplier = silver_wounding_multiplier(
        target_traits.injury_multiplier("silver"), construction
    )
    value = level(compiled, weapon.skill_id)
    actor_hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    validate_rated_strength(equipment.profile_id, weapon, st)
    aim = actor.maneuver_state
    aimed = (
        pending.area_aim_point is None
        and (aim.aim_item_id, aim.aim_mode_id, aim.aim_target_id)
        == (
            pending.weapon_id,
            weapon.id,
            aim_target_id,
        )
        and aim.aim_seconds > 0
        and not pending.vehicle_aim_lost
        and pending.tactical_approach != "pop-up"
    )
    bonus = (
        pending.suppression_aim_bonus
        if pending.suppression_zone_id is not None
        else aim.aim_bonus
        - aim.aim_sight_bonus
        + optical_bonus(state, actor.actor_id, aim.aim_sight_bonus)
        if aimed
        else 0
    )
    if pending.suppression_zone_id is None:
        bonus = _maneuver_bonus(actor, weapon, bonus)
    bonus += accuracy_bonus(
        compiled,
        runtime.reviewer.compiler.definitions,
        weapon,
        aimed=aimed,
        suppressed=pending.suppression_zone_id is not None,
    )
    effective_shots = pending.shots
    close_projectile_multiplier = 1
    if weapon.multiple_projectiles is not None:
        assert weapon.half_damage_range is not None
        effective_shots, close_projectile_multiplier = multiple_projectile_attack(
            pending.shots,
            weapon.multiple_projectiles.projectiles_per_shot,
            attack_distance,
            float(weapon.half_damage_range),
        )
    range_modifier = (
        weapon.bulk
        if pending.close_combat
        else range_penalty(
            attack_distance
            + (0 if pending.area_aim_point is not None else scene.speed_yards_per_second)
        )
    )
    attack_target = (
        int(value.value)
        + pending.visibility_attack_penalty
        + bonus
        + (
            4
            if pending.area_aim_point is not None
            else scene.size_modifier + size_delta(state.resources, target.actor_id)
        )
        + range_modifier
        + rapid_fire_bonus(effective_shots)
        # A mount bears the weapon, so the firer's own ST is not what limits it.
        - (0 if weapon.mount is not None else minimum_strength_penalty(weapon.minimum_st, st))
        + pending.vehicle_attack_penalty
        + (-2 if pending.tactical_approach == "pop-up" else 0)
    )
    attack_target -= 2 * bool(pending.stray_target_order)
    if (
        pending.laser_sight
        and scene.laser_visible_to_firer
        and not acute_blindness(state.resources, actor.actor_id)
    ):
        attack_target += 1
    if pending.target_item_id:
        attack_target += (
            target_modifier(runtime, state, target.actor_id, pending.target_item_id)
            - scene.size_modifier
            - size_delta(state.resources, target.actor_id)
        )
    attack_target += entangle_attack_penalty(actor)
    attack_target -= actor_hp.injury.shock if actor_hp.injury else 0
    if (
        actor_hp.injury
        and pending.suppression_zone_id is None
        and not acute_blindness(state.resources, actor.actor_id)
    ):
        attack_target += actor_hp.injury.physical_traits.darkness(encounter.darkness_penalty)

    eyes = disabled(state.resources, actor.actor_id) & {"left-eye", "right-eye"}
    if eyes and not acute_blindness(state.resources, actor.actor_id):
        attack_target -= (
            6 if len(eyes) == 2 else 1 if aimed and actor.last_maneuver != "move_and_attack" else 3
        )
    shield_side = next(
        (
            hand.split("-")[0]
            for item, hand in target.hand_bindings
            if any(i.id == item and entries[i.definition_id].shield for i in state.resources.items)
        ),
        None,
    )
    attack_target += targeted_attack_penalty(
        pending.hit_location,
        armor_chink=pending.armor_chink,
        damage_type=weapon.damage.damage_type,
        tight_beam=weapon.damage.tight_beam,
        shield_side=shield_side,
    )
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
    defense_value_ = _visibility_adjustment(
        defense_value_,
        external_defense_penalty(state, target.actor_id, pending.visibility_defense_penalty)
        + pending.attention_defense_penalty,
    )
    second_value = _visibility_adjustment(
        second_value,
        external_defense_penalty(state, target.actor_id, pending.visibility_defense_penalty)
        + pending.attention_defense_penalty,
    )
    laser_visible = (
        pending.laser_sight
        and scene.laser_visible_to_target
        and not acute_blindness(state.resources, target.actor_id)
    )
    defense_value_ = _laser_defense(defense_value_, selected, laser_visible)
    second_value = _laser_defense(second_value, second_defense, laser_visible)
    if weapon.thrown:
        thrown_item = next(i for i in state.resources.items if i.id == pending.weapon_id)
        entry = next(e for e in equipment.entries if e.definition_id == thrown_item.definition_id)
        penalty = 2 if entry.weight_millipounds <= 1000 else 1
        if selected == "parry" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value - penalty, ())
        if second_defense == "parry" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value - penalty, ())

    spec = AttackRollSpec(
        profile_id=equipment.profile_id,
        target=attack_target,
        modifiers=check_modifiers(state.resources, actor.actor_id, "dx"),
        ranged=True,
        rule_id="gurps.combat.ranged_attack",
    )
    if prepare_only:
        return spec
    state = state.model_copy(
        update={"resources": before_attack(state.resources, pending.weapon_id, weapon)}
    )
    state = _apply_one_handed_readiness(state, actor.actor_id, pending.weapon_id, weapon, st)
    attack = (
        pending.attack_roll
        or selected_attack
        or success_roll(
            equipment.profile_id,
            attack_target,
            check_modifiers(state.resources, actor.actor_id, "dx"),
            rng=runtime.rng,
        )
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

    if pending.protected_defender_id and attack.outcome is Outcome.CRITICAL_SUCCESS:
        unintercepted = pending.model_copy(
            update={
                "defender_id": pending.protected_defender_id,
                "protected_defender_id": None,
                "attack_roll": attack,
            }
        )
        return resolve(
            runtime,
            state,
            encounter.model_copy(update={"pending_defense": unintercepted}),
            weapon,
            "none",
            None,
            second_defense=None,
            second_item_id=None,
        )
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
        if pending.protected_defender_id:
            hits = (
                hits
                if defense.outcome.succeeded
                and not (pending.sacrificial_drop and defense.margin >= 3)
                else 0
            )
        elif defense.outcome.succeeded:
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
    if pending.suppression_zone_id is None and not (
        pending.protected_defender_id and defense is not None and not defense.outcome.succeeded
    ):
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
        attack=attack,
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

    dr_bonus = 0

    if runtime.rules.abilities is not None:
        dr_bonus = damage_resistance(
            state.resources, target.actor_id, build_revision=defender_build.revision
        )
    environmental_dr = scene.beam_environment_dr if weapon.beam_environment_dr else 0

    vehicle_cover = _target_vehicle_cover(state, encounter, target)
    dr = (
        _damage_armor_dr(runtime, state, target.actor_id, location)
        + dr_bonus
        + environmental_dr
        + vehicle_cover
    ) * close_projectile_multiplier
    first_location, first_location_dice, first_dr = location, location_dice, dr
    expression = stats.swing if weapon.damage.basis == "swing" else stats.thrust
    range_st = st
    if weapon.rated_strength is not None:
        range_st = weapon.rated_strength.st
        expression = strength_damage(equipment.profile_id, range_st)[0]
    count = (weapon.damage.dice or expression.dice) * close_projectile_multiplier
    adds = (
        weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    ) * close_projectile_multiplier
    weapon_id = next(i.definition_id for i in original_resources.items if i.id == pending.weapon_id)
    adds += damage_bonus(
        compiled,
        weapon_id,
        weapon.skill_id,
        weapon.hands,
        count,
        applies=weapon.damage.basis != "fixed",
    )
    # Guided/homing tables use the apparent 1/2D cell as projectile speed,
    # not as a damage falloff threshold (B281 notes 3-4).
    half = (
        weapon.guidance is None
        and weapon.half_damage_range is not None
        and scene.distance
        >= float(weapon.half_damage_range) * (range_st if weapon.range_basis == "st" else 1)
    )
    resistance_damage = (
        weapon.damage
        if close_projectile_multiplier == 1
        else weapon.damage.model_copy(
            update={
                "armor_divisor": weapon.damage.armor_divisor / close_projectile_multiplier,
            }
        )
    )
    if half and weapon.loses_armor_divisor_at_half_range:
        resistance_damage = resistance_damage.model_copy(update={"armor_divisor": Decimal(1)})
    resistance_weapon = (
        weapon
        if close_projectile_multiplier == 1
        else weapon.model_copy(update={"damage": resistance_damage})
    )
    context = RangedDamageContext(
        pending=pending,
        weapon=weapon,
        construction=construction,
        actor=actor,
        target=target,
        original_target=original_target,
        compiled=compiled,
        defender_build=defender_build,
        hp=hp,
        hp_before=hp.current,
        attack=attack,
        original_attack=original_attack,
        defense=defense,
        second_trace=second_trace,
        value=value,
        defense_value=defense_value_,
        scene=scene,
        critical=critical,
        critical_eye=critical_eye,
        critical_table=critical_table,
        critical_rolls=critical_rolls,
        critical_parry_mode=critical_parry_mode,
        parry_item=parry_item,
        head=head,
        blocked=blocked,
        close_projectile_multiplier=close_projectile_multiplier,
        shield_hit=shield_hit,
        shield_impacts=shield_impacts,
        hits=hits,
        impacts=impacts,
        shots_fired=shots_fired,
        effective_shots=effective_shots,
        malfunction_table=malfunction_table,
        failure=failure,
        follow_up=follow_up,
        vulnerability_multiplier=vulnerability_multiplier,
        count=count,
        adds=adds,
        half=half,
        resistance_damage=resistance_damage,
        resistance_weapon=resistance_weapon,
        dr_bonus=dr_bonus,
        environmental_dr=environmental_dr,
        vehicle_cover=vehicle_cover,
        base_location=base_location,
        base_location_dice=base_location_dice,
        first_location=first_location,
        first_location_dice=first_location_dice,
        first_dr=first_dr,
        original_ammunition=next(
            (
                load
                for load in original_resources.ammunition_loads
                if load.weapon_id == pending.weapon_id
            ),
            None,
        ),
        original_items=tuple(
            item
            for item in original_resources.items
            if item.owner_id in (actor.actor_id, target.actor_id)
        ),
        original_pools=tuple(
            pool
            for pool in original_resources.pools
            if pool.id
            in (
                "hp:" + actor.actor_id,
                "fp:" + actor.actor_id,
                "hp:" + target.actor_id,
                "fp:" + target.actor_id,
            )
        ),
    )
    progress = RangedDamageProgress(
        location=location,
        location_dice=location_dice,
        dr=dr,
        effect_dice=effect_dice,
        lasting_ids=lasting_ids,
    )
    return advance_ranged_damage(
        runtime, state, encounter, context, progress, pause=prepare_damage, secret=secret_damage
    )


def refresh_ranged_damage_target(
    runtime: RulesContext, state: PlayState, encounter: Encounter, prepared: PreparedRangedDamage
) -> PreparedRangedDamage:
    """Keep this projectile's delivery and location while refreshing unresolved injury."""
    context, progress = prepared.context, prepared.progress
    target = next(p for p in encounter.participants if p.actor_id == context.target.actor_id)
    compiled = build(runtime, state, target.actor_id)
    hp = next(p for p in state.resources.pools if p.id == context.hp.id)
    traits = attack_defense_traits(compiled, runtime.reviewer.compiler.definitions)
    bonus = (
        damage_resistance(state.resources, target.actor_id, build_revision=compiled.revision)
        if runtime.rules.abilities is not None
        else 0
    )
    cover = _target_vehicle_cover(state, encounter, target)
    dr = (
        _damage_armor_dr(runtime, state, target.actor_id, progress.location)
        + bonus
        + context.environmental_dr
        + cover
    ) * context.close_projectile_multiplier
    return prepared.model_copy(
        update={
            "context": context.model_copy(
                update={
                    "target": target,
                    "defender_build": compiled,
                    "hp": hp,
                    "hp_before": hp.current if progress.index == 0 else context.hp_before,
                    "dr_bonus": bonus,
                    "vehicle_cover": cover,
                    "first_dr": dr if progress.index == 0 else context.first_dr,
                    "vulnerability_multiplier": silver_wounding_multiplier(
                        traits.injury_multiplier("silver"), context.construction
                    ),
                }
            ),
            "progress": progress.model_copy(
                update={
                    "dr": dr,
                    "hit_resistances": progress.hit_resistances[:-1] + (dr,),
                }
            ),
        }
    )


def advance_ranged_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    context: RangedDamageContext,
    progress: RangedDamageProgress,
    *,
    pause: bool = True,
    secret: bool = False,
    selected_damage: tuple[int, ...] | None = None,
) -> tuple[PlayState, Encounter, InjuryTrace] | RangedDamageStage:
    if selected_damage is not None:
        if (
            progress.index >= context.impacts
            or len(selected_damage) != context.count
            or any(type(die) is not int or not 1 <= die <= 6 for die in selected_damage)
        ):
            raise ValidationError("Selected ranged damage disagrees with its captured expression")
        state, encounter, progress = _apply_ranged_impact(
            runtime, state, encounter, context, progress, selected_damage, False
        )
    while progress.index < (context.impacts if context.blocked is None else 0):
        progress, maximum = _prepare_ranged_impact(runtime, state, context, progress)
        if pause and not maximum:
            original = None if secret else draw_dice(runtime.rng, context.count)
            state, encounter = _sync_ranged_pending(state, encounter, context)
            return RangedDamageStage(
                state,
                encounter,
                PreparedRangedDamage(
                    context=context, progress=progress, original=original, secret=secret
                ),
            )
        dice = () if maximum else draw_dice(runtime.rng, context.count)
        state, encounter, progress = _apply_ranged_impact(
            runtime, state, encounter, context, progress, dice, maximum
        )
    return _finish_ranged_damage(runtime, state, encounter, context, progress)


def _sync_ranged_pending(
    state: PlayState, encounter: Encounter, context: RangedDamageContext
) -> tuple[PlayState, Encounter]:
    hp = next(pool for pool in state.resources.pools if pool.id == context.hp.id)
    assert hp.injury is not None
    target = next(
        actor for actor in encounter.participants if actor.actor_id == context.target.actor_id
    )
    target = target.model_copy(
        update={
            "posture": "prone" if hp.injury.prone else target.posture,
            "forced_do_nothing": target.forced_do_nothing or context.target.forced_do_nothing,
            "ready_item_ids": tuple(
                sorted(
                    item.id
                    for item in state.resources.items
                    if item.owner_id == target.actor_id and item.ready and item.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target)
    if hp.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(
                        update={
                            "conditions": tuple(dict.fromkeys((*actor.conditions, "unconscious")))
                        }
                    )
                    if actor.actor_id == target.actor_id
                    else actor
                    for actor in state.actors
                )
            }
        )
    return state, encounter


def _prepare_ranged_impact(
    runtime: RulesContext,
    state: PlayState,
    context: RangedDamageContext,
    progress: RangedDamageProgress,
) -> tuple[RangedDamageProgress, bool]:
    pending = context.pending
    actor = context.actor
    target = context.target
    hp = context.hp
    attack = context.attack
    critical = context.critical
    head = context.head
    close_projectile_multiplier = context.close_projectile_multiplier
    dr_bonus = context.dr_bonus
    vehicle_cover = context.vehicle_cover
    base_location = context.base_location
    base_location_dice = context.base_location_dice
    index = progress.index
    location = progress.location
    location_dice = progress.location_dice
    dr = progress.dr
    hit_resistances = list(progress.hit_resistances)
    hit_locations = list(progress.hit_locations)
    hit_location_dice = list(progress.hit_location_dice)
    equipment = catalog(runtime)
    if index and pending.hit_location == "random":
        location, location_dice = select_location(
            "random",
            rng=runtime.rng,
            from_behind=from_behind(actor, target),
        )
        current_hp = next(p for p in state.resources.pools if p.id == hp.id)
        if current_hp.injury and missing_location(current_hp.injury, location):
            location = "torso"
        dr = (
            _damage_armor_dr(runtime, state, target.actor_id, location) + dr_bonus + vehicle_cover
        ) * close_projectile_multiplier
    elif index and location != base_location:
        location, location_dice = base_location, base_location_dice
        dr = (
            _damage_armor_dr(runtime, state, target.actor_id, location) + dr_bonus + vehicle_cover
        ) * close_projectile_multiplier
    hit_resistances.append(dr)
    hit_locations.append(location)
    hit_location_dice.append(location_dice)
    hit_critical = critical if index == 0 else 0
    maximum = hit_critical in ((3, 15) if head else (6, 15)) or (
        equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
    )
    return progress.model_copy(
        update={
            "location": location,
            "location_dice": location_dice,
            "dr": dr,
            "hit_resistances": tuple(hit_resistances),
            "hit_locations": tuple(hit_locations),
            "hit_location_dice": tuple(hit_location_dice),
        }
    ), maximum


def _apply_ranged_impact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    context: RangedDamageContext,
    progress: RangedDamageProgress,
    dice: tuple[int, ...],
    maximum: bool,
) -> tuple[PlayState, Encounter, RangedDamageProgress]:
    pending = context.pending
    weapon = context.weapon
    target = context.target
    hp = context.hp
    scene = context.scene
    critical_eye = context.critical_eye
    head = context.head
    close_projectile_multiplier = context.close_projectile_multiplier
    shield_hit = context.shield_hit
    shield_impacts = context.shield_impacts
    follow_up = context.follow_up
    vulnerability_multiplier = context.vulnerability_multiplier
    count = context.count
    adds = context.adds
    half = context.half
    resistance_damage = context.resistance_damage
    resistance_weapon = context.resistance_weapon
    dr_bonus = context.dr_bonus
    vehicle_cover = context.vehicle_cover
    index = progress.index
    location = progress.location
    location_dice = progress.location_dice
    dr = progress.dr
    damages = list(progress.damages)
    injuries = list(progress.injuries)
    damage_dice = list(progress.damage_dice)
    hit_resistances = list(progress.hit_resistances)
    hit_locations = list(progress.hit_locations)
    hit_location_dice = list(progress.hit_location_dice)
    effect_dice = progress.effect_dice
    lasting_ids = progress.lasting_ids
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    defender_stats = context.defender_build.statistics
    assert defender_stats is not None
    hit_critical = context.critical if progress.index == 0 else 0
    damage_dice.extend(dice)
    damage = (
        max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * count if maximum else sum(dice)) + adds,
        )
        * weapon.damage.multiplier
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

    rolled_damage = damage
    intervening_dr = 0

    if pending.target_item_id:
        state, encounter, object_result = damage_target(
            runtime,
            state,
            encounter,
            pending.target_item_id,
            damage,
            resistance_damage.model_copy(
                update={
                    "armor_divisor": resistance_damage.armor_divisor
                    * (2 if pending.armor_chink else 1)
                }
            ),
            impact=index,
        )
        damages.append(damage)
        injuries.append(0)
        hit_resistances[-1] = object_result.effective_dr if object_result else 0
        if object_result:
            effect_dice += tuple(d for roll in object_result.checks for d in roll)
        target_item = next(i for i in state.resources.items if i.id == pending.target_item_id)
        target_profile = entries[target_item.definition_id]
        state = state.model_copy(
            update={
                "resources": persist_surge(
                    state.resources,
                    event_id=f"{pending.id}:surge:{index}",
                    target_item_id=target_item.id,
                    penetrating_damage=object_result.injury if object_result else 0,
                    target_is_electrical=bool(
                        target_profile.electronics
                        or any(
                            isinstance(mode, RangedMode) and mode.smartgun is not None
                            for mode in target_profile.modes
                        )
                    ),
                )
                if weapon.damage.surge
                else state.resources
            }
        )
        return (
            state,
            encounter,
            RangedDamageProgress(
                index=index + 1,
                location=location,
                location_dice=location_dice,
                dr=dr,
                damages=tuple(damages),
                injuries=tuple(injuries),
                damage_dice=tuple(damage_dice),
                hit_resistances=tuple(hit_resistances),
                hit_locations=tuple(hit_locations),
                hit_location_dice=tuple(hit_location_dice),
                effect_dice=effect_dice,
                lasting_ids=lasting_ids,
            ),
        )
    state, encounter, intervening_dr = _resolve_cover_impact(
        runtime,
        state,
        encounter,
        resistance_damage,
        cover_item_id=pending.cover_item_id,
        projectile_id=f"{pending.id}:hit:{index}",
        target_id=target.actor_id,
        basic_damage=damage,
    )
    dr += intervening_dr
    hit_resistances[-1] = dr
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
            return (
                state,
                encounter,
                RangedDamageProgress(
                    index=index + 1,
                    location=location,
                    location_dice=location_dice,
                    dr=dr,
                    damages=tuple(damages),
                    injuries=tuple(injuries),
                    damage_dice=tuple(damage_dice),
                    hit_resistances=tuple(hit_resistances),
                    hit_locations=tuple(hit_locations),
                    hit_location_dice=tuple(hit_location_dice),
                    effect_dice=effect_dice,
                    lasting_ids=lasting_ids,
                ),
            )
        effect_dice = _shield_side_effect(
            runtime,
            state,
            encounter,
            target.actor_id,
            shield_hit,
            (_damage_armor_dr(runtime, state, target.actor_id, location) + dr_bonus + vehicle_cover)
            * close_projectile_multiplier,
            effect_dice,
            hit_locations,
            hit_resistances,
        )
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
            armor_divisor=(
                Decimal(1)
                if half and weapon.loses_armor_divisor_at_half_range
                else weapon.damage.armor_divisor
            )
            * (2 if pending.armor_chink else 1),
            tight_beam=weapon.damage.tight_beam,
            vulnerability_multiplier=vulnerability_multiplier,
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
    state, follow_up_dice = _resolve_follow_up_hit(
        runtime,
        state,
        event_id=f"{pending.id}:follow-up:{index}",
        target_actor_id=target.actor_id,
        target_ht=defender_stats.ht,
        resistance_dr=_damage_armor_dr(runtime, state, target.actor_id, location) + dr_bonus,
        injury=result.injury,
        spec=follow_up,
    )
    effect_dice += follow_up_dice
    state, encounter = _resolve_overpenetration(
        runtime,
        state,
        encounter,
        weapon,
        secondary_id=pending.overpenetration_target_id,
        projectile_id=f"{pending.id}:hit:{index}",
        rolled_damage=rolled_damage,
        intervening_dr=intervening_dr,
        primary_hp=hp.maximum,
        primary_armor_dr=_damage_armor_dr(runtime, state, target.actor_id, location) + dr_bonus,
        blocked_by_shield=shield_hit is not None,
        location=location,
    )
    return (
        state,
        encounter,
        RangedDamageProgress(
            index=index + 1,
            location=location,
            location_dice=location_dice,
            dr=dr,
            damages=tuple(damages),
            injuries=tuple(injuries),
            damage_dice=tuple(damage_dice),
            hit_resistances=tuple(hit_resistances),
            hit_locations=tuple(hit_locations),
            hit_location_dice=tuple(hit_location_dice),
            effect_dice=effect_dice,
            lasting_ids=lasting_ids,
        ),
    )


def _finish_ranged_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    context: RangedDamageContext,
    progress: RangedDamageProgress,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = context.pending
    weapon = context.weapon
    actor = context.actor
    target = context.target
    original_target = context.original_target
    compiled = context.compiled
    defender_build = context.defender_build
    hp = context.hp
    attack = context.attack
    original_attack = context.original_attack
    defense = context.defense
    second_trace = context.second_trace
    value = context.value
    defense_value_ = context.defense_value
    scene = context.scene
    critical = context.critical
    critical_table = context.critical_table
    critical_rolls = context.critical_rolls
    critical_parry_mode = context.critical_parry_mode
    parry_item = context.parry_item
    head = context.head
    blocked = context.blocked
    hits = context.hits
    impacts = context.impacts
    shots_fired = context.shots_fired
    effective_shots = context.effective_shots
    malfunction_table = context.malfunction_table
    failure = context.failure
    first_location = context.first_location
    first_location_dice = context.first_location_dice
    first_dr = context.first_dr
    damages = list(progress.damages)
    injuries = list(progress.injuries)
    damage_dice = list(progress.damage_dice)
    hit_resistances = list(progress.hit_resistances)
    hit_locations = list(progress.hit_locations)
    hit_location_dice = list(progress.hit_location_dice)
    effect_dice = progress.effect_dice
    lasting_ids = progress.lasting_ids
    equipment = catalog(runtime)
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
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
        hp_before=context.hp_before,
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
                        ammunition_load=context.original_ammunition,
                        items=context.original_items,
                        pools=context.original_pools,
                        failure=failure,
                        trace=trace,
                    ),
                )
            }
        )
    state = state.model_copy(
        update={
            "resources": persist_back_blast(
                state.resources,
                event_id=pending.id + ":back-blast",
                weapon_item_id=pending.weapon_id,
                mode=weapon,
                shots_fired=shots_fired,
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
                        ammunition_load=context.original_ammunition,
                        items=context.original_items,
                        pools=context.original_pools,
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


def _target_vehicle_cover(state: PlayState, encounter: Encounter, target: Combatant) -> int:
    return max(
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


def _damage_armor_dr(
    runtime: RulesContext, state: PlayState, target_id: str, location: HumanLocation | None
) -> int:
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    return max(
        (
            armor.dr
            for i in state.resources.items
            if i.owner_id == target_id
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
