"""Held Fireball release through the existing defense pause and injury service."""

from typing import Literal, overload

from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.objects.combat import (
    damage_target,
    intercepting_shield,
    shield_damage,
    target_modifier,
)
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.special_melee import targeted_attack_penalty
from wayfarer.engine.simulation.combat.visibility import adjusted_defense, external_defense_penalty
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import Damage
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.magic.missile_damage_records import (
    MissileDamageInputs,
    MissileDamageStage,
    PreparedMissileDamage,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEvent as SpellEvent,
)
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellResult,
    event_id,
    latest,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


@overload
def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
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
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
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
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
    selected_attack: CheckTrace | None = None,
    prepare_only: Literal[False] = False,
    prepare_damage: Literal[True],
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace] | MissileDamageStage: ...


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
    selected_attack: CheckTrace | None = None,
    prepare_only: bool = False,
    prepare_damage: bool = False,
    secret_damage: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace] | AttackRollSpec | MissileDamageStage:
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

    penalty = external_defense_penalty(state, defender.actor_id, pending.visibility_defense_penalty)
    defense = adjusted_defense(defense, penalty)
    second = adjusted_defense(second, penalty)
    object_penalty = (
        target_modifier(runtime, state, defender.actor_id, pending.target_item_id)
        if pending.target_item_id
        else 0
    )
    spec = AttackRollSpec(
        profile_id=PROFILE,
        target=(
            int(value.value)
            + pending.visibility_attack_penalty
            + pending.spell_aim_bonus
            + range_penalty(distance)
            + object_penalty
            - (actor_hp.injury.shock if actor_hp.injury else 0)
            + targeted_attack_penalty(
                pending.hit_location,
                armor_chink=False,
                damage_type="burn",
                tight_beam=False,
                shield_side=None,
            )
        ),
        modifiers=check_modifiers(state.resources, attacker.actor_id, "dx"),
        ranged=True,
        rule_id="gurps.combat.ranged_attack",
    )
    if prepare_only:
        return spec
    attack = selected_attack or spec.roll(runtime.rng)
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

    shield_hit = intercepting_shield(runtime, state, encounter, second_roll or defended)
    maximum = row in (6, 15)

    inputs = MissileDamageInputs(
        pending=pending,
        effect=effect,
        attacker_id=attacker.actor_id,
        defender=defender,
        target_ht=defend_build.statistics.ht,
        hp_before=hp.current,
        attack=attack,
        defended=defended,
        second_roll=second_roll,
        value=value,
        defense=defense,
        critical=critical,
        row=row,
        blocked=blocked,
        shield_hit=shield_hit,
        maximum=maximum,
        hit=hit,
        distance=distance,
        # This explicit engine route uses B378 at 1/2D; ordinary histories keep their generation.
        generation="pending-damage" if prepare_damage else "legacy",
        resistance=armor(runtime, state, defender.actor_id),
        dropped=tuple(sorted(dropped)),
    )
    if prepare_damage and inputs.rollable:
        original = None if secret_damage else draw_dice(runtime.rng, effect.energy)
        encounter = CombatEngine._replace(encounter, defender)
        return MissileDamageStage(
            state,
            encounter,
            PreparedMissileDamage(inputs=inputs, original=original, secret=secret_damage),
        )
    return finish_missile_damage(runtime, state, encounter, inputs)


def validate_missile_damage(
    state: PlayState, encounter: Encounter, inputs: MissileDamageInputs
) -> None:
    current = latest(state.resources).get(inputs.effect.cast_id)
    if (
        encounter.pending_defense != inputs.pending
        or current is None
        or current.model_dump() != inputs.effect.model_dump()
    ):
        raise ValidationError("Captured missile delivery or held spell changed")


def refresh_missile_damage_target(
    runtime: RulesContext, state: PlayState, encounter: Encounter, inputs: MissileDamageInputs
) -> MissileDamageInputs:
    validate_missile_damage(state, encounter, inputs)
    target = build(runtime, state, inputs.pending.defender_id)
    assert target.statistics is not None
    return inputs.model_copy(
        update={
            "defender": next(
                p for p in encounter.participants if p.actor_id == inputs.pending.defender_id
            ),
            "target_ht": target.statistics.ht,
            "hp_before": next(
                p.current
                for p in state.resources.pools
                if p.id == "hp:" + inputs.pending.defender_id
            ),
            "resistance": armor(runtime, state, inputs.pending.defender_id),
        }
    )


def finish_missile_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    inputs: MissileDamageInputs,
    *,
    selected_damage: tuple[int, ...] | None = None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending, effect = inputs.pending, inputs.effect
    validate_missile_damage(state, encounter, inputs)
    if selected_damage is not None and (
        not inputs.rollable
        or len(selected_damage) != effect.energy
        or any(type(die) is not int or not 1 <= die <= 6 for die in selected_damage)
    ):
        raise ValidationError("Selected missile damage differs from its captured energy")
    defender = inputs.defender
    row, maximum, hit, blocked = inputs.row, inputs.maximum, inputs.hit, inputs.blocked
    shield_hit = inputs.shield_hit
    dropped = set(inputs.dropped)
    resources = state.resources
    dice = (
        (selected_damage if selected_damage is not None else draw_dice(runtime.rng, effect.energy))
        if inputs.rollable
        else ()
    )
    damage = 6 * effect.energy if maximum else sum(dice)
    damage *= 3 if row in (3, 18) else 2 if row in (5, 16) else 1
    damage //= 2 if inputs.half_damage else 1
    dr = inputs.resistance
    lost = 0
    if damage and (shield_hit or pending.target_item_id):
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
                location=pending.hit_location,
            ),
            ht=inputs.target_ht,
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
                    target_id=inputs.attacker_id,
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
    if inputs.generation == "pending-damage":
        current_hp = next(p for p in resources.pools if p.id == "hp:" + defender.actor_id)
        if current_hp.injury and current_hp.injury.prone:
            defender = defender.model_copy(update={"posture": "prone"})
    encounter = CombatEngine._replace(encounter, defender)

    encounter = distracted(
        runtime,
        state.model_copy(update={"resources": resources}),
        encounter,
        defender.actor_id,
        defended=inputs.defended is not None,
        injured=lost > 0,
    )
    if blocked:
        encounter = encounter.model_copy(update={"blocked_reason": "ranged-critical-table"})
    updated_hp = next(p for p in resources.pools if p.id == "hp:" + defender.actor_id)
    return (
        state.model_copy(update={"resources": resources}),
        encounter,
        InjuryTrace(
            attack=inputs.attack,
            defense=inputs.defended,
            second_defense=inputs.second_roll,
            attack_value=inputs.value,
            defense_value=inputs.defense,
            damage_dice=dice,
            basic_damage=damage,
            resistance=dr,
            injury=lost,
            hp_before=inputs.hp_before,
            hp_after=updated_hp.current,
            incapacitated=bool(updated_hp.injury and updated_hp.injury.incapacitated),
            profile_id=PROFILE,
            rules_version=BASELINE_ID,
            critical_table=inputs.critical,
            adjudication_required="ranged-critical-table" if blocked else None,
            shots_fired=1,
            hits=int(hit and not blocked),
            per_hit_damage=(damage,) if hit and not blocked else (),
            per_hit_injury=(lost,) if hit and not blocked else (),
        ),
    )
