"""Profile-selected weapon melee within the existing encounter transaction.

Numeric baseline: Lite (August 2004), pp. 24-28; Basic Set B369-376,
B381-382 and B556. Complex Basic critical-miss consequences stop runtime with a
persisted table result rather than silently substituting ordinary damage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, overload

import wayfarer.engine.simulation.combat.criticals.limbs as critical_limbs
from wayfarer.engine.character.statistics import damage as strength_damage
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.character.traits.mastery import damage_bonus
from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty, strong_damage_bonus
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.critical import IncomingWound
from wayfarer.engine.simulation.combat.criticals.context import capture_critical
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, PendingDefense
from wayfarer.engine.simulation.combat.entangle import attack_penalty as entangle_attack_penalty
from wayfarer.engine.simulation.combat.equipment_effects import defense_stress
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.maneuvers import attack_modifier
from wayfarer.engine.simulation.combat.melee.damage_records import (
    MeleeDamageInputs,
    MeleeDamageStage,
    PreparedMeleeDamage,
)
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.electrical import (
    electrical_armor,
    resolve_cattle_prod,
)
from wayfarer.engine.simulation.combat.melee.heavy_parry import resolve_heavy_parry
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.objects.combat import (
    critical_breakage,
    damage_target,
    intercepting_shield,
    shield_damage,
    shock,
    target_modifier,
)
from wayfarer.engine.simulation.combat.objects.locations import from_behind, unavailable_hand
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.ranged.resolution import resolve
from wayfarer.engine.simulation.combat.special_melee import actor_reaches, targeted_attack_penalty
from wayfarer.engine.simulation.combat.tactical import height_effect
from wayfarer.engine.simulation.combat.thrown.flight import position, resolve_flight
from wayfarer.engine.simulation.combat.unarmed.records import striking_bonus
from wayfarer.engine.simulation.combat.visibility import external_defense_penalty, melee_eye_penalty
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import Armor, MeleeMode, RangedMode
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
from wayfarer.engine.simulation.magic.missiles import resolve as resolve_spell
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.skills.power_blow import power_blow_strength
from wayfarer.errors import ValidationError


def _visibility_adjustment(value: DerivedValue | None, penalty: int) -> DerivedValue | None:
    if value is None:
        return None
    return DerivedValue(value.target, value.value + penalty, value.explanations)


def _armor_resistance(cattle_prod: bool, armors: tuple[Armor, ...]) -> tuple[int, int, bool]:
    if cattle_prod:
        return electrical_armor(armors)
    return max((armor.dr for armor in armors), default=0), 0, False


def _apply_cattle_prod(
    state: PlayState,
    *,
    enabled: bool,
    event_id: str,
    target_id: str,
    ht: int,
    armor_bonus: int,
    insulated: bool,
    contact_seconds: int,
    rng: RandomSource,
    effect_dice: tuple[int, ...],
) -> tuple[PlayState, tuple[int, ...]]:
    if not enabled:
        return state, effect_dice
    resources, electrical = resolve_cattle_prod(
        state.resources,
        event_id=event_id,
        target_id=target_id,
        ht=ht,
        armor_bonus=armor_bonus,
        insulated=insulated,
        contact_seconds=contact_seconds,
        rng=rng,
    )
    if electrical.resistance is not None:
        effect_dice += electrical.resistance.dice
    return state.model_copy(update={"resources": resources}), effect_dice


if TYPE_CHECKING:
    from wayfarer.engine.simulation.magic.melee_spell_state import MeleeSpellContact


def _current_contact(
    runtime: RulesContext, state: PlayState, pending_id: str
) -> MeleeSpellContact | None:
    # deferred: held-Melee admission shares the CombatEngine/RulesContext cycle.
    from wayfarer.engine.simulation.magic.melee_spell_admission import validate_contact

    # deferred: held-Melee state shares the CombatEngine/RulesContext cycle.
    from wayfarer.engine.simulation.magic.melee_spell_state import read_contact

    contact = read_contact(state.resources, pending_id)
    if contact is not None:
        validate_contact(runtime, state, contact)
    return contact


def _damage_contact(
    runtime: RulesContext,
    state: PlayState,
    inputs: MeleeDamageInputs,
    *,
    prevalidated: bool = False,
) -> MeleeSpellContact | None:
    if not prevalidated:
        return _current_contact(runtime, state, inputs.pending.id)
    # deferred: private held-Melee facts share the CombatEngine/RulesContext cycle.
    from wayfarer.engine.simulation.magic.melee_spell_state import read_contact

    return read_contact(state.resources, inputs.pending.id)


def _settle_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    contact: MeleeSpellContact | None,
    inputs: MeleeDamageInputs,
    hit: bool,
) -> tuple[PlayState, Encounter, int]:
    if contact is None:
        return state, encounter, 0
    # deferred: held-Melee settlement shares the CombatEngine/RulesContext cycle.
    from wayfarer.engine.simulation.magic.melee_spell_transitions import finish_contact

    implement = inputs.defense_item
    state, encounter, result = finish_contact(
        runtime,
        state,
        encounter,
        contact,
        ordinary_hit=hit,
        actual_defense=inputs.contact_defense,
        defense_check=inputs.defense,
        defense_implement_id=None if implement in ("left-hand", "right-hand") else implement,
        critical_row=inputs.critical,
    )
    return state, encounter, result.injury


def _route_weapon(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    parry_mode_id: str | None,
    second_parry_mode_id: str | None,
    catch_thrown: bool,
    selected_attack: CheckTrace | None,
) -> MeleeMode | tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None
    if pending.spell_cast_id is not None:
        return resolve_spell(
            runtime,
            state,
            encounter,
            selected,
            item_id,
            second_defense,
            second_item_id,
            selected_attack=selected_attack,
        )
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if isinstance(weapon, RangedMode):
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
            selected_attack=selected_attack,
        )
    return weapon


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[False] = False,
    prepare_damage: Literal[False] = False,
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]: ...


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[True],
    prepare_damage: Literal[False] = False,
    secret_damage: bool = False,
) -> AttackRollSpec: ...


@overload
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
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[False] = False,
    prepare_damage: Literal[True],
    secret_damage: bool = False,
) -> MeleeDamageStage: ...


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
    selected_attack: CheckTrace | None = None,
    prepare_only: bool = False,
    prepare_damage: bool = False,
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace] | AttackRollSpec | MeleeDamageStage:
    pending = encounter.pending_defense
    assert pending is not None
    # Validate before attack/defense dice. Later same-transaction critical
    # consequences cannot retroactively invalidate the already accepted contact.
    contact = _current_contact(runtime, state, pending.id)
    if contact is not None:
        if prepare_only or prepare_damage or secret_damage:
            raise ValidationError("Held Melee contact does not yet support Luck damage choices")
        if second_defense is not None:
            raise ValidationError("Held Melee contact requires one actual active defense")
    if (prepare_only or prepare_damage) and (
        pending.spell_cast_id is not None
        or isinstance(
            mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id),
            RangedMode,
        )
    ):
        raise ValidationError("Prepare ranged and missile attacks through their dedicated route")
    routed = _route_weapon(
        runtime,
        state,
        encounter,
        selected,
        item_id,
        second_defense,
        second_item_id,
        parry_mode_id,
        second_parry_mode_id,
        catch_thrown,
        selected_attack,
    )
    if isinstance(routed, tuple):
        return routed
    weapon = routed
    equipment = catalog(runtime)
    weapon_item = next(item for item in state.resources.items if item.id == pending.weapon_id)
    cattle_prod = weapon_item.definition_id == "equipment:cattle-prod"
    damage_type = "cr" if pending.subdual_mode is not None else weapon.damage.damage_type
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    aim_target = next(
        p
        for p in encounter.participants
        if p.actor_id == (pending.protected_defender_id or pending.defender_id)
    )
    attack_build = build(runtime, state, pending.attacker_id)
    defend_build = build(runtime, state, pending.defender_id)
    assert attack_build.statistics is not None and defend_build.statistics is not None
    construction = attack_construction(state.resources, pending.weapon_id)
    target_traits = attack_defense_traits(defend_build, runtime.reviewer.compiler.definitions)
    vulnerability_multiplier = silver_wounding_multiplier(
        target_traits.injury_multiplier("silver"), construction
    )
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
        min(int(attack_value.value), pending.mounted_skill_cap or int(attack_value.value))
        + pending.visibility_attack_penalty
        + (
            0
            if acute_blindness(state.resources, pending.attacker_id)
            else attacker_hp.injury.physical_traits.darkness(encounter.darkness_penalty)
        )
        - attacker_hp.injury.shock
        - (
            minimum_strength_penalty(
                weapon.minimum_st, fatigue_value(attacker_fp, attack_build.statistics.st)
            )
            if weapon.minimum_st is not None
            else 0
        )
    )

    attack_target -= shock(state, pending.weapon_id)
    if pending.target_item_id:
        attack_target += target_modifier(
            runtime, state, pending.defender_id, pending.target_item_id
        )
    attack_target -= 4 if attacker.grappled else 0
    attack_target -= (
        4 if attacker.posture == "prone" else 2 if attacker.posture == "kneeling" else 0
    )
    attack_target -= 2 * bool(pending.stray_target_order)

    attack_target -= melee_eye_penalty(state, pending.attacker_id)

    reaches = actor_reaches(runtime, state, attacker.actor_id, weapon.reach)
    height = height_effect(
        encounter,
        attacker,
        aim_target,
        reach=max(reaches),
        location=pending.hit_location,
        board=runtime.hex_map(encounter),
    )
    attack_target += height.attack_modifier
    entries = {e.definition_id: e for e in equipment.entries}
    shield_side = next(
        (
            hand.split("-")[0]
            for item, hand in defender.hand_bindings
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
    attack_target += entangle_attack_penalty(attacker)
    if (
        aim_target.unarmed_guard_dropped
        and attacker.maneuver_state.evaluate_target_id == aim_target.actor_id
    ):
        attack_target += attacker.maneuver_state.evaluate_bonus
    attack_target = attack_modifier(
        attacker.maneuver_state,
        aim_target.actor_id,
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
    defense_derived = _visibility_adjustment(
        defense_derived,
        external_defense_penalty(state, pending.defender_id, pending.visibility_defense_penalty)
        + pending.attention_defense_penalty,
    )
    second_derived = _visibility_adjustment(
        second_derived,
        external_defense_penalty(state, pending.defender_id, pending.visibility_defense_penalty)
        + pending.attention_defense_penalty,
    )
    spec = AttackRollSpec(
        profile_id=equipment.profile_id,
        target=attack_target,
        modifiers=check_modifiers(state.resources, attacker.actor_id, "dx"),
    )
    if prepare_only:
        return spec
    attack = pending.attack_roll or selected_attack or spec.roll(runtime.rng)
    if pending.protected_defender_id and attack.outcome is Outcome.CRITICAL_SUCCESS:
        # B375 uses ordinary Dodge rules: critical attacks cannot be intercepted.
        unintercepted = pending.model_copy(
            update={
                "defender_id": pending.protected_defender_id,
                "protected_defender_id": None,
                "attack_roll": attack,
            }
        )
        return resolve_melee(
            runtime,
            state,
            encounter.model_copy(update={"pending_defense": unintercepted}),
            "none",
            None,
        )
    defense = None
    second_trace = None

    near_miss = torso_near_miss(pending.hit_location, attack)
    if near_miss:
        try:
            height_effect(
                encounter,
                attacker,
                defender,
                reach=max(reaches),
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

    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_derived is not None:
        defense = success_roll(equipment.profile_id, int(defense_derived.value), rng=runtime.rng)
        hit = (
            defense.outcome.succeeded
            if pending.protected_defender_id
            else not defense.outcome.succeeded
        )
        if selected == "parry" and defense_item:
            defender = defender.model_copy(update={"parries": defender.parries + (defense_item,)})
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if (
            defense.outcome.succeeded
            and selected == "parry"
            and defense_item not in ("left-hand", "right-hand")
            and intercepting_shield(runtime, state, encounter, defense, require_durable=False)
            is None
        ):
            assert defense_item is not None
            state, defender, parry_dice, stopped = resolve_heavy_parry(
                runtime, state, encounter, defender, defense_item
            )
            effect_dice += parry_dice
            if not stopped:
                hit = True
                blocked = None
        bare_failure = (
            contact is not None
            and selected == "parry"
            and defense_item in ("left-hand", "right-hand")
            and defense.outcome is Outcome.CRITICAL_FAILURE
        )
        if bare_failure:
            # B557 applies to the actual bare limb, never the Staff weapon table.
            # deferred: held-Melee and unarmed adapters share the CombatEngine/RulesContext cycle.
            from wayfarer.engine.simulation.combat.unarmed.injury import critical_miss

            # deferred: held-Melee and unarmed adapters share the CombatEngine/RulesContext cycle.
            from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed

            assert defense_item in ("left-hand", "right-hand")
            critical_dice = draw_dice(runtime.rng, 3)
            bare_hand: Literal["left-hand", "right-hand"] = (
                "left-hand" if defense_item == "left-hand" else "right-hand"
            )
            local = PendingUnarmed(
                id=pending.id,
                actor_id=attacker.actor_id,
                target_id=defender.actor_id,
                action="punch",
                skill="attribute:dx",
                hands=(bare_hand,),
                allowed=("none",),
            )
            encounter = encounter.model_copy(
                update={
                    "participants": tuple(
                        defender if p.actor_id == defender.actor_id else p
                        for p in encounter.participants
                    )
                }
            )
            state, encounter, bare_checks, extra, handled = critical_miss(
                runtime, state, encounter, local, defender.actor_id, critical_dice, defense_item
            )
            # deferred: held-Melee and unarmed adapters share the CombatEngine/RulesContext cycle.
            from wayfarer.engine.simulation.combat.melee.damage_records import BareContactCritical

            # deferred: held-Melee and unarmed adapters share the CombatEngine/RulesContext cycle.
            from wayfarer.engine.simulation.magic.melee_spell_state import append

            state = state.model_copy(
                update={
                    "resources": append(
                        state.resources,
                        "bare-critical",
                        pending.id,
                        defender.actor_id,
                        BareContactCritical(
                            pending_id=pending.id,
                            defender_id=defender.actor_id,
                            hand=bare_hand,
                            table=critical_dice,
                            checks=bare_checks,
                            effect_dice=extra,
                            handled=handled,
                        ),
                    )
                }
            )
            effect_dice += extra
            defender = next(p for p in encounter.participants if p.actor_id == defender.actor_id)
            blocked = (
                None
                if handled
                else f"basic-unarmed-defense-critical:critical-failure:{sum(critical_dice)}"
            )
            hit = handled
        elif equipment.profile_id == "gurps-basic-set-4e-2004" and (
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
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    defender if p.actor_id == defender.actor_id else p
                    for p in encounter.participants
                )
            }
        )
        parry_miss = blocked.endswith(":defender")
        state, encounter, limb = critical_limbs.resolve_limb(
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
        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=runtime.rng,
            from_behind=from_behind(attacker, defender),
        )
        if pending.hit_location == "random" and missing_location(hp.injury, location):
            location = "torso"
    head = (
        location in ("face", "skull", "left-eye", "right-eye")
        and damage_type != "tox"
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    if head and critical in (6, 7) and location in ("face", "skull"):
        if from_behind(attacker, defender) or (hp.injury.tolerance and hp.injury.tolerance.no_eyes):
            critical = 4
        else:
            eye_die = draw_dice(runtime.rng, 1)[0]
            effect_dice += (eye_die,)
            location = "right-eye" if eye_die <= 3 else "left-eye"
            critical_eye = True
    if head and critical == 8:
        defender = defender.model_copy(update={"forced_do_nothing": True})
    thrust, swing = strength_damage(
        equipment.profile_id,
        power_blow_strength(
            state.resources,
            attacker.actor_id,
            attack_build.revision,
            pending.id,
            encounter.id,
            pending.opened_round,
            pending.opened_turn,
            pending.strike_strength
            if pending.strike_strength is not None
            else attack_build.statistics.st,
            pending.weapon_id,
            pending.mode_id,
        ),
    )
    expression = swing if weapon.damage.basis == "swing" else thrust
    dice_count = pending.mounted_lance_dice or (
        (weapon.damage.dice or expression.dice) + weapon.damage.bonus_dice
    )
    adds = (
        3
        if pending.mounted_lance_dice
        else weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    )
    if weapon.punch_damage:
        adds += (
            striking_bonus(
                weapon.skill_id,
                attack_build.statistics.dx,
                int(level(attack_build, weapon.skill_id).value),
            )
            * dice_count
        )
    adds += damage_bonus(
        attack_build,
        weapon_item.definition_id,
        weapon.skill_id,
        weapon.hands,
        dice_count,
        applies=weapon.damage.basis != "fixed" and not pending.mounted_lance_dice,
    )
    adds -= int(pending.subdual_mode == "blunt-end")
    adds += attacker.maneuver_state.stop_thrust_damage_bonus
    if attacker.maneuver_state.strong:
        adds += strong_damage_bonus(dice_count)

    shield_hit = intercepting_shield(runtime, state, encounter, second_trace or defense)
    maximum = critical in ((3, 15) if head else (6, 15)) or (
        equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
    )
    if contact is not None:
        # The physical receipt starts after any separate B557 self-injury.
        hp = next(p for p in state.resources.pools if p.id == hp.id)
    inputs = MeleeDamageInputs(
        pending=pending,
        weapon=weapon,
        construction=construction,
        attack_build=attack_build,
        defend_build=defend_build,
        attacker=attacker,
        defender=defender,
        hp=hp,
        attack=attack,
        defense=defense,
        second_trace=second_trace,
        attack_value=attack_value,
        defense_derived=defense_derived,
        defense_item=defense_item,
        critical=critical,
        critical_dice=critical_dice,
        critical_tables=critical_tables,
        critical_parry_mode=critical_parry_mode,
        critical_eye=critical_eye,
        blocked=blocked,
        cattle_prod=cattle_prod,
        damage_type=damage_type,
        head=head,
        hit=hit,
        shield_hit=shield_hit,
        maximum=maximum,
        dice_count=dice_count,
        adds=adds,
        effect_dice=effect_dice,
        lasting_ids=lasting_ids,
        location=location,
        location_dice=location_dice,
        vulnerability_multiplier=vulnerability_multiplier,
        contact_defense=selected if contact is not None else "none",
    )
    if prepare_damage:
        original = (
            (None if secret_damage else draw_dice(runtime.rng, dice_count))
            if inputs.rollable
            else ()
        )
        return MeleeDamageStage(
            state,
            encounter,
            PreparedMeleeDamage(inputs=inputs, original=original, secret=secret_damage),
        )
    return finish_melee_damage(
        runtime, state, encounter, inputs, contact_prevalidated=contact is not None
    )


def refresh_melee_damage_target(
    runtime: RulesContext, state: PlayState, encounter: Encounter, inputs: MeleeDamageInputs
) -> MeleeDamageInputs:
    """Refresh only injury facts; delivery, location and the damage expression stay fixed."""
    target = build(runtime, state, inputs.pending.defender_id)
    traits = attack_defense_traits(target, runtime.reviewer.compiler.definitions)
    return inputs.model_copy(
        update={
            "defend_build": target,
            "defender": next(
                p for p in encounter.participants if p.actor_id == inputs.pending.defender_id
            ),
            "hp": next(p for p in state.resources.pools if p.id == inputs.hp.id),
            "vulnerability_multiplier": silver_wounding_multiplier(
                traits.injury_multiplier("silver"), inputs.construction
            ),
        }
    )


def finish_melee_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    inputs: MeleeDamageInputs,
    *,
    selected_damage: tuple[int, ...] | None = None,
    contact_prevalidated: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    """Apply one fixed melee delivery's selected damage through its actual tail."""
    pending = inputs.pending
    contact = _damage_contact(runtime, state, inputs, prevalidated=contact_prevalidated)
    weapon = inputs.weapon
    attack_build = inputs.attack_build
    defend_build = inputs.defend_build
    attacker = inputs.attacker
    defender = inputs.defender
    hp = inputs.hp
    attack = inputs.attack
    defense = inputs.defense
    second_trace = inputs.second_trace
    attack_value = inputs.attack_value
    defense_derived = inputs.defense_derived
    defense_item = inputs.defense_item
    critical = inputs.critical
    critical_dice = inputs.critical_dice
    critical_tables = inputs.critical_tables
    critical_parry_mode = inputs.critical_parry_mode
    critical_eye = inputs.critical_eye
    blocked = inputs.blocked
    cattle_prod = inputs.cattle_prod
    damage_type = inputs.damage_type
    head = inputs.head
    hit = inputs.hit
    shield_hit = inputs.shield_hit
    maximum = inputs.maximum
    dice_count = inputs.dice_count
    adds = inputs.adds
    effect_dice = inputs.effect_dice
    lasting_ids = inputs.lasting_ids
    location = inputs.location
    location_dice = inputs.location_dice
    vulnerability_multiplier = inputs.vulnerability_multiplier
    equipment = catalog(runtime)
    assert attack_build.statistics is not None and defend_build.statistics is not None
    if selected_damage is not None and (
        not inputs.rollable
        or len(selected_damage) != dice_count
        or any(type(die) is not int or not 1 <= die <= 6 for die in selected_damage)
    ):
        raise ValidationError("Selected melee damage disagrees with its captured expression")
    dice = (
        selected_damage
        if selected_damage is not None
        else draw_dice(runtime.rng, dice_count)
        if (hit or shield_hit) and not maximum
        else ()
    )
    basic = (
        max(
            0 if damage_type == "cr" else 1,
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
    covering_armor = tuple(
        e.armor
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
    )
    resistance, electrical_bonus, electrically_insulated = _armor_resistance(
        cattle_prod, covering_armor
    )

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
        object_damage = weapon.damage.model_copy(
            update={
                "armor_divisor": weapon.damage.armor_divisor
                * (2 if half else 1)
                * (2 if pending.armor_chink else 1)
            }
        )
        state, encounter, object_result = damage_target(
            runtime,
            state,
            encounter,
            pending.target_item_id,
            basic,
            object_damage,
            impact=0,
        )
        assert object_result is not None
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
                damage_type=damage_type,
                location=location,
                armor_divisor=weapon.damage.armor_divisor * (2 if pending.armor_chink else 1),
                tight_beam=weapon.damage.tight_beam,
                critical_eye=critical_eye,
                vulnerability_multiplier=vulnerability_multiplier,
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
                if head and critical in (12, 13) and damage_type == "cr"
                else "scarred"
                if head and critical in (12, 13)
                else None
            ),
            scar_levels=2 if damage_type in ("burn", "cor") else 1,
        )
        injury = result.injury
        resistance = result.effective_resistance
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
        state = state.model_copy(update={"resources": resources})
    state, effect_dice = _apply_cattle_prod(
        state,
        enabled=hit and cattle_prod and pending.target_item_id is None,
        event_id=pending.id,
        target_id=pending.defender_id,
        ht=defend_build.statistics.ht,
        armor_bonus=electrical_bonus,
        insulated=electrically_insulated,
        contact_seconds=pending.electrical_contact_seconds,
        rng=runtime.rng,
        effect_dice=effect_dice,
    )
    updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
    status = updated_hp.injury
    assert status is not None
    physical_status = status
    state, encounter, magical_injury = _settle_contact(
        runtime, state, encounter, contact, inputs, hit
    )
    combined_hp = next(p for p in state.resources.pools if p.id == hp.id)
    assert combined_hp.injury is not None
    status = combined_hp.injury
    state, drop_dice = _critical_drop_items(
        runtime, state, defender, pending, held, head=head, critical=critical
    )
    effect_dice += drop_dice
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
        state, encounter, flight_dice = resolve_flight(runtime, state, encounter, critical_dice)
        effect_dice += flight_dice
        updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
        status = updated_hp.injury
        assert status is not None
        injury = hp.current - updated_hp.current
        blocked = None
        encounter = encounter.model_copy(update={"blocked_reason": None})
    if blocked and blocked.startswith("basic-critical-miss:"):
        incoming = (
            IncomingWound(
                actor_id=defender.actor_id,
                dice=dice_count,
                adds=adds,
                damage_type=damage_type,
                resistance=resistance,
                ht=defend_build.statistics.ht,
                dx=defend_build.statistics.dx,
                hit_location=pending.hit_location,
                armor_divisor=weapon.damage.armor_divisor * (2 if pending.armor_chink else 1),
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
        incapacitated=physical_status.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_dice,
        adjudication_required=blocked,
        location=location,
        location_dice=location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
    )

    encounter = distracted(
        runtime,
        state,
        encounter,
        defender.actor_id,
        defended=defense is not None,
        injured=injury > 0 or magical_injury > 0,
    )
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    stuck = weapon.can_stick and injury > 0 and hit
    if weapon.becomes_unready_after_attack(attack_build.statistics.st) or stuck:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "ready": False,
                                    **(
                                        {
                                            "stuck_target_id": pending.defender_id,
                                            "stuck_injury": injury,
                                            "stuck_damage_type": damage_type,
                                        }
                                        if stuck
                                        else {}
                                    ),
                                }
                            )
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


def _critical_drop_items(
    runtime: RulesContext,
    state: PlayState,
    defender: Combatant,
    pending: PendingDefense,
    held: tuple[str, ...],
    *,
    head: bool,
    critical: int | None,
) -> tuple[PlayState, tuple[int, ...]]:
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    effect_dice: tuple[int, ...] = ()
    drops = held if critical == 12 and not head and not pending.target_item_id else ()
    weapons = tuple(
        i
        for i in held
        if entries[next(item.definition_id for item in state.resources.items if item.id == i)].modes
    )
    if head and critical == 14 and weapons:
        if len(weapons) > 1:
            drop_die = draw_dice(runtime.rng, 1)[0]
            effect_dice = (drop_die,)
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
    return state, effect_dice
