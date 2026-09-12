"""Held Fireball release through the existing defense pause and injury service."""

from dataclasses import replace

from wayfarer.engine.rules.checks import Outcome, draw_dice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.ranged_tables import range_penalty
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat import CombatEngine, Defense, Encounter, InjuryTrace
from wayfarer.engine.simulation.condition_checks import check_modifiers
from wayfarer.engine.simulation.injury import Wound, apply_injury
from wayfarer.engine.simulation.mechanics.gurps_melee import build, defense_value, level
from wayfarer.engine.simulation.mechanics.spell_effects import armor
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.spells import PROFILE, SpellEvent, SpellResult, event_id, latest
from wayfarer.errors import ValidationError


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None and pending.spell_cast_id is not None
    effect = latest(state.resources).get(pending.spell_cast_id)
    if (
        effect is None
        or effect.phase != "active"
        or effect.spell_id != "fireball"
        or not effect.execute_effects
    ):
        raise ValidationError("Missile is no longer held")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attack_build = build(runtime, state, attacker.actor_id)
    defend_build = build(runtime, state, defender.actor_id)
    assert defend_build.statistics
    if attack_build.revision != effect.build_revision:
        raise ValidationError("Missile build changed")
    value = level(attack_build, "skill:innate-attack-projectile")
    distance = CombatEngine.distance(attacker.position, defender.position)
    if distance > 50:
        raise ValidationError("Fireball exceeds its maximum range")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + defender.actor_id)
    actor_hp = next(p for p in state.resources.pools if p.id == "hp:" + attacker.actor_id)
    defense, used_item = defense_value(runtime, state, defender, selected, item_id)
    second, second_item = defense_value(
        runtime, state, defender, second_defense or "none", second_item_id
    )
    from wayfarer.engine.simulation.mechanics.object_combat import target_modifier

    object_penalty = (
        target_modifier(runtime, state, defender.actor_id, pending.target_item_id)
        if pending.target_item_id
        else 0
    )
    attack = success_roll(
        PROFILE,
        int(value.value)
        + range_penalty(distance)
        + object_penalty
        - (actor_hp.injury.shock if actor_hp.injury else 0),
        check_modifiers(state.resources, attacker.actor_id, "dx"),
        rng=runtime.rng,
    )
    attack = replace(attack, rule_id="gurps.combat.ranged_attack")
    if attack.outcome is Outcome.CRITICAL_FAILURE and attack.total < 17:
        attack = replace(attack, outcome=Outcome.FAILURE)
    defended = None
    second_roll = None
    hit = attack.outcome.succeeded
    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense is not None:
        defended = success_roll(PROFILE, int(defense.value), rng=runtime.rng)
        hit = not defended.outcome.succeeded
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if hit and second is not None:
            second_roll = success_roll(PROFILE, int(second.value), rng=runtime.rng)
            hit = not second_roll.outcome.succeeded
            if second_defense == "block":
                defender = defender.model_copy(update={"block_used": True})
    dropped: set[str] = set()
    for choice, roll, equipment_id in (
        (selected, defended, used_item),
        (second_defense, second_roll, second_item),
    ):
        if roll and roll.outcome is Outcome.CRITICAL_FAILURE:
            if choice == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif choice == "block" and equipment_id:
                dropped.add(equipment_id)
    resources = state.resources
    # B556 body criticals use the same injury options as physical missiles.
    critical = (
        draw_dice(runtime.rng, 3)
        if attack.outcome in (Outcome.CRITICAL_SUCCESS, Outcome.CRITICAL_FAILURE)
        else ()
    )
    row = sum(critical) if attack.outcome is Outcome.CRITICAL_SUCCESS else 0
    blocked = attack.outcome is Outcome.CRITICAL_FAILURE
    if blocked and sum(critical) in (7, 13, 16):
        attacker = attacker.model_copy(update={"defense_penalty": -2})
        encounter = CombatEngine._replace(encounter, attacker)
        blocked = False
    from wayfarer.engine.simulation.mechanics.object_combat import intercepting_shield

    shield_hit = intercepting_shield(runtime, state, encounter, second_roll or defended)
    maximum = row in (6, 15)
    dice = (
        draw_dice(runtime.rng, effect.energy)
        if (hit or shield_hit) and not blocked and not maximum
        else ()
    )
    damage = 6 * effect.energy if maximum else sum(dice)
    damage *= 3 if row in (3, 18) else 2 if row in (5, 16) else 1
    damage //= 2 if distance > 25 else 1
    dr = armor(runtime, state, defender.actor_id)
    lost = 0
    if damage and (shield_hit or pending.target_item_id):
        from wayfarer.engine.simulation.gurps_equipment import Damage
        from wayfarer.engine.simulation.mechanics.object_combat import damage_target, shield_damage

        missile = Damage(basis="fixed", dice=effect.energy, damage_type="burn")
        if pending.target_item_id:
            state, encounter, _ = damage_target(
                runtime,
                state,
                encounter,
                pending.target_item_id,
                damage,
                missile,
                impact=0,
            )
            damage = 0
        elif shield_hit:
            state, encounter, damage = shield_damage(
                runtime,
                state,
                encounter,
                shield_hit,
                damage,
                missile,
            )
        resources = state.resources
    if damage:
        held = tuple(
            i.id
            for i in resources.items
            if i.id in {item for item, _ in defender.hand_bindings} and i.ready and i.equipped
        )
        resources, injury = apply_injury(
            resources,
            Wound(
                id=event_id(pending.id) + ":impact",
                actor_id=defender.actor_id,
                expected_revision=resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type="burn",
            ),
            ht=defend_build.statistics.ht,
            rng=runtime.rng,
            system=True,
            held_item_ids=held,
            force_major_wound=row in (7, 13, 14),
            double_shock=row == 8,
            funny_bone=row == 8,
            halve_dr="down" if row in (4, 17) else None,
        )
        lost = injury.injury
        if row == 12:
            dropped.update(held)
    resources = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"ready": False}) if i.id in dropped else i
                for i in resources.items
            ),
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id(pending.id) + ":released",
                    at=resources.game_time,
                    target_id=attacker.actor_id,
                    kind=SpellEvent(
                        effect=effect.model_copy(update={"phase": "ended"}),
                        result=SpellResult(outcome="released"),
                    ).model_dump_json(),
                ),
            ),
        }
    )
    ready = {i.id for i in resources.items if i.ready and i.equipped}
    defender = defender.model_copy(
        update={"ready_item_ids": tuple(i for i in defender.ready_item_ids if i in ready)}
    )
    encounter = CombatEngine._replace(encounter, defender)
    from wayfarer.engine.simulation.mechanics.gurps_maneuvers import distracted

    encounter = distracted(
        runtime,
        state.model_copy(update={"resources": resources}),
        encounter,
        defender.actor_id,
        defended=defended is not None,
        injured=lost > 0,
    )
    if blocked:
        encounter = encounter.model_copy(update={"blocked_reason": "ranged-critical-table"})
    updated_hp = next(p for p in resources.pools if p.id == hp.id)
    return (
        state.model_copy(update={"resources": resources}),
        encounter,
        InjuryTrace(
            attack=attack,
            defense=defended,
            second_defense=second_roll,
            attack_value=value,
            defense_value=defense,
            damage_dice=dice,
            basic_damage=damage,
            resistance=dr,
            injury=lost,
            hp_before=hp.current,
            hp_after=updated_hp.current,
            incapacitated=bool(updated_hp.injury and updated_hp.injury.incapacitated),
            profile_id=PROFILE,
            rules_version=BASELINE_ID,
            critical_table=critical,
            adjudication_required="ranged-critical-table" if blocked else None,
            shots_fired=1,
            hits=int(hit and not blocked),
            per_hit_damage=(damage,) if hit and not blocked else (),
            per_hit_injury=(lost,) if hit and not blocked else (),
        ),
    )
