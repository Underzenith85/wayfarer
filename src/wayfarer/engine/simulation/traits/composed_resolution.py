"""B61/B201 delivery, B375 defense, and canonical composed consequences."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, draw_dice
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.abilities import damage_resistance, interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, injury_turn
from wayfarer.engine.simulation.combat.attack_roll import score_attack
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuver_transitions import distracted
from wayfarer.engine.simulation.combat.melee.defense import (
    defense_value,
    exert_defense,
    validate_defense_choices,
)
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.visibility import external_defense_penalty
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.cyclic_host_state import bind_occurrence
from wayfarer.engine.simulation.health.hit_locations import effective_dr
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.attack_defense import (
    AttackChannel,
    PreparedOwnerDamage,
    TraitAttackCommand,
    TraitAttackConsequences,
    TraitAttackOutcome,
    _id,
    apply_trait_attack,
)
from wayfarer.engine.simulation.traits.composed_attacks import _malediction_checks
from wayfarer.engine.simulation.traits.composed_host import (
    CurrentAttack,
    ResistComposedAttack,
    attack_target,
    preflight_pending,
)
from wayfarer.engine.simulation.traits.composed_phases import (
    ComposedAttackChoice,
    ComposedDelivery,
    OwnerDamageArguments,
)
from wayfarer.engine.simulation.traits.composed_sources import (
    PROFILE,
    ComposedPending,
    finish_binding,
    identity,
    pending_binding,
    raw_build,
)
from wayfarer.engine.simulation.traits.innate_criticals import (
    InnateCriticalContext,
    InnateCriticalSource,
    InnateHitEffects,
    apply_fatigue_major_wound,
    body_hit_effects,
    classify_ranged_check,
    critical_dodge_failure,
    drop_all_held,
    drop_held_items,
    resolve_innate_miss,
)
from wayfarer.engine.simulation.traits.malediction_checks import (
    prepare_resister,
    resolve_malediction,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

RESOLUTION_PREFIX = "composed-resolution:"


class ComposedResolution(Record):
    command_id: str
    binding_id: str
    selected: Literal["none", "dodge"]
    outcome: TraitAttackOutcome
    trace: InjuryTrace
    critical_id: str | None = None


@dataclass(frozen=True)
class ResolvedAttack:
    state: PlayState
    encounter: Encounter
    result: CombatResult
    outcome: TraitAttackOutcome


def _roll(
    runtime: RulesContext, state: PlayState, encounter: Encounter, current: CurrentAttack
) -> CheckTrace:
    target = attack_target(runtime, state, encounter, current)
    return classify_ranged_check(
        success_roll(
            PROFILE,
            target,
            modifiers=check_modifiers(state.resources, current.source.actor_id, "dx"),
            rng=runtime.rng,
        )
    )


def critical_context(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    binding: ComposedPending,
    current: CurrentAttack,
) -> InnateCriticalContext:
    source = current.source
    attacker = build(runtime, state, source.actor_id)
    assert attacker.statistics is not None
    defensive_attacker = build(runtime, state, source.actor_id, defensive=True)
    assert defensive_attacker.statistics is not None
    actor = next(p for p in encounter.participants if p.actor_id == source.actor_id)
    gear = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
    entries = {e.definition_id: e for e in gear.entries} if gear else {}
    natural = attack_defense_traits(
        current.attacker, runtime.reviewer.compiler.definitions
    ).damage_resistance()
    active = damage_resistance(
        state.resources, source.actor_id, build_revision=current.attacker.revision
    )
    protection = []
    for limb in ("left-arm", "right-arm", "left-leg", "right-leg"):
        broad = "arms" if limb.endswith("arm") else "legs"
        worn = max(
            (
                entry.armor.dr
                for item in state.resources.items
                if item.owner_id == source.actor_id
                and item.equipped
                and (item.condition is None or not item.condition.disabled)
                for entry in (entries[item.definition_id],)
                if entry.armor and (limb in entry.armor.locations or broad in entry.armor.locations)
            ),
            default=0,
        )
        protection.append((limb, natural + active + worn))
    return InnateCriticalContext.model_validate(
        {
            "id": identity("innate-critical:", binding.id),
            "campaign_id": state.campaign_id,
            "encounter_id": encounter.id,
            "pending_id": binding.pending_id,
            "attacker_id": binding.attacker_id,
            "target_id": binding.target_id,
            "location_id": current.context.location_id,
            "target_build_revision": current.target.revision,
            "created_at": state.resources.game_time,
            "attacker_ht": attacker.statistics.ht,
            "attacker_dx": attacker.statistics.dx,
            "attacker_resistance_ht": defensive_attacker.statistics.ht,
            "attacker_traits": attack_defense_traits(
                attacker, runtime.reviewer.compiler.definitions
            ),
            "limb_dr": tuple(protection),
            "held_item_ids": tuple(dict.fromkeys(i for i, _ in actor.hand_bindings)),
            "hand_bindings": actor.hand_bindings,
            "source": InnateCriticalSource(
                source_id=source.id,
                build_revision=source.build_revision,
                description_digest=hashlib.sha256(source.description.encode()).hexdigest(),
                specialty=source.specialty,
                damage_dice=source.damage_dice,
                damage_type=source.damage_type,
                armor_divisor=source.profile.armor_divisor,
                emitter_arm=source.emitter_limb,
                modifier_profile=source.profile,
                contagion_vector=source.contagion_vector,
                incubation_seconds=source.incubation_seconds,
            ),
        }
    )


def finish(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    binding: ComposedPending,
    selected: Literal["none", "dodge"],
    command_id: str,
    trace: InjuryTrace,
    *,
    captured_attacker: ValidatedBuild | None = None,
) -> tuple[PlayState, Encounter, CombatResult]:
    engine = runtime.combat
    assert engine is not None
    pending = encounter.pending_defense
    assert pending is not None
    state = injury_turn(
        runtime,
        state,
        binding.attacker_id,
        binding.command_id,
        start=False,
        do_nothing=False,
        captured_end_build=captured_attacker
        if next(a for a in state.actors if a.actor_id == binding.attacker_id).approval is None
        else None,
    )
    # Validated source and target choice already determine this existing vocabulary.
    if selected not in ("none", "dodge"):
        raise ValidationError("Unsupported composed defense")
    if binding.stage == "resistance":
        attacker = next(p for p in encounter.participants if p.actor_id == binding.attacker_id)
        encounter = engine._replace(
            encounter,
            attacker.model_copy(
                update={
                    "maneuver_state": attacker.maneuver_state.model_copy(
                        update={"concentrating": False, "concentration_seconds": 0}
                    )
                }
            ),
        )
    encounter, result = engine.choose_defense(
        encounter, actor_id=binding.target_id, selected=selected
    )
    resources = finish_binding(state.resources, binding, "resolved:" + command_id)
    state = state.model_copy(update={"resources": resources})
    encounter = encounter.model_copy(update={"wounds": encounter.wounds + (trace,)})
    result = result.model_copy(
        update={
            "code": "combat.resolved",
            "injury": trace,
            "round": encounter.round,
            "current_actor_id": encounter.current_actor_id,
            "available": engine.available(encounter, encounter.current_actor_id),
        }
    )
    return state, encounter, result


@dataclass(frozen=True)
class Delivery:
    state: PlayState
    encounter: Encounter
    current: CurrentAttack
    hit: bool
    defended: bool
    checks: tuple[CheckTrace, ...]
    selected: Literal["none", "dodge"]
    defense: CheckTrace | None
    value: DerivedValue | None
    effects: InnateHitEffects
    critical_table: tuple[int, ...]
    critical_id: str | None


def _resisted_delivery(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    binding: ComposedPending,
    current: CurrentAttack,
    command: ResistComposedAttack,
    selected_attack: ComposedAttackChoice | None,
) -> tuple[bool, tuple[CheckTrace, ...]]:
    pending = encounter.pending_defense
    assert pending is not None
    source = current.source
    if binding.stage != "resistance" or command.pending_id != pending.id:
        raise ConflictError("This pending use is not the selected Malediction resistance")
    actor = next(p for p in encounter.participants if p.actor_id == binding.attacker_id)
    if selected_attack is None and not actor.maneuver_state.concentrating:
        raise ConflictError("Malediction concentration was interrupted; abandon the spent use")
    if selected_attack is not None:
        prepared = selected_attack.malediction
        if prepared is None or prepared.resist != command.resist:
            raise ConflictError("Malediction resistance differs from its captured declaration")
        if prepared.resist and selected_attack.selected.outcome.succeeded:
            prepared = prepared.model_copy(
                update={
                    "resister": prepare_resister(
                        state.resources,
                        build(runtime, state, binding.target_id, defensive=True),
                        source.profile,
                        current.context,
                        runtime.reviewer.compiler.definitions,
                        current_conditions=True,
                    )
                }
            )
        hit, checks = resolve_malediction(
            prepared, rng=runtime.rng, selected_attack=selected_attack.selected
        )
    else:
        hit, checks = _malediction_checks(
            state.resources,
            binding.attacker_id,
            build(runtime, state, binding.attacker_id),
            build(runtime, state, binding.target_id, defensive=True),
            source.profile,
            current.context.model_copy(update={"resist": command.resist}),
            runtime.reviewer.compiler.definitions,
            runtime.rng,
            current_conditions=True,
        )
    return hit, checks


def _delivery(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    binding: ComposedPending,
    current: CurrentAttack,
    command: ChooseDefense | ResistComposedAttack,
    *,
    selected_attack: ComposedAttackChoice | None = None,
) -> Delivery:
    pending = encounter.pending_defense
    assert pending is not None
    source = current.source
    effects = InnateHitEffects()
    critical_table: tuple[int, ...] = ()
    critical_id = None
    defense = None
    value = None
    selected: Literal["none", "dodge"] = "none"
    if isinstance(command, ResistComposedAttack):
        hit, checks = _resisted_delivery(
            runtime, state, encounter, binding, current, command, selected_attack
        )
        attack = checks[0]
        defended = False
    else:
        if binding.stage != "defense":
            raise ValidationError("Malediction requires a target-controlled resistance response")
        if command.defense not in ("none", "dodge") or command.second_defense is not None:
            raise ValidationError("This approved ranged source permits Dodge or no defense")
        validate_defense_choices(
            runtime,
            state,
            encounter,
            command.defense,
            command.item_id,
            command.second_defense,
            command.second_item_id,
        )
        selected = command.defense
        if selected != "none":
            state = state.model_copy(
                update={
                    "resources": interrupt_concentration(
                        state.resources, command.actor_id, command.id, distraction=True
                    )
                }
            )
        state, encounter, exerted = exert_defense(
            runtime, state, encounter, command.actor_id, command.id, selected, command.item_id
        )
        if exerted not in ("none", "dodge"):
            raise ValidationError("Composed source permits only Dodge")
        selected = "none" if exerted == "none" else "dodge"
        # Gear stress can change current armor; resolve it at impact, not declaration.
        current = (
            _current_impact(runtime, state, binding, current)
            if selected_attack is not None
            else preflight_pending(runtime, state, encounter)
        )
        target = next(p for p in encounter.participants if p.actor_id == binding.target_id)
        value, _ = defense_value(runtime, state, target, selected, command.item_id)
        if value is not None:
            value = DerivedValue(
                value.target,
                value.value
                + external_defense_penalty(
                    state, target.actor_id, pending.visibility_defense_penalty
                ),
                value.explanations,
            )
        attack = (
            selected_attack.selected
            if selected_attack is not None
            else _roll(runtime, state, encounter, current)
        )
        hit = attack.outcome.succeeded
        if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and value is not None:
            defense = success_roll(PROFILE, int(value.value), rng=runtime.rng)
            resources, encounter = critical_dodge_failure(
                state.resources, encounter, binding.target_id, defense
            )
            state = state.model_copy(update={"resources": resources})
        defended = defense is not None and defense.outcome.succeeded
        checks = (attack,) + (() if defense is None else (defense,))
        if attack.outcome is Outcome.CRITICAL_SUCCESS:
            table = draw_dice(runtime.rng)
            critical_table = table
            effects = body_hit_effects(table)
        elif attack.outcome is Outcome.CRITICAL_FAILURE:
            context = critical_context(runtime, state, encounter, binding, current)
            resources, encounter, miss = resolve_innate_miss(
                state.resources, encounter, context, attack, rng=runtime.rng, system=True
            )
            state = state.model_copy(update={"resources": resources})
            if miss.cyclic_attack_id is not None:
                if source.cyclic_policy is None:
                    raise ValidationError(
                        "Self-hit Cyclic consequence requires its approved stopping policy"
                    )
                resources = bind_occurrence(
                    resources,
                    miss.cyclic_attack_id,
                    source_id=source.id,
                    source_revision=source.source_revision,
                    policy=source.cyclic_policy,
                    location=miss.location,
                )
                state = state.model_copy(update={"resources": resources})
            critical_id = miss.critical_id
    return Delivery(
        state,
        encounter,
        current,
        hit,
        defended,
        checks,
        selected,
        defense,
        value,
        effects,
        critical_table,
        critical_id,
    )


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense | ResistComposedAttack,
    *,
    selected_attack: ComposedAttackChoice | None = None,
) -> ResolvedAttack:
    state, encounter, delivery = prepare_delivery(
        runtime, state, encounter, command, selected_attack=selected_attack
    )
    return finish_delivery(runtime, state, encounter, delivery)


def _current_impact(
    runtime: RulesContext, state: PlayState, binding: ComposedPending, current: CurrentAttack
) -> CurrentAttack:
    """Refresh the victim's protection, never a launched attack's source or target."""
    target = raw_build(runtime, state, binding.target_id)
    natural = attack_defense_traits(
        target, runtime.reviewer.compiler.definitions
    ).damage_resistance()
    return replace(
        current, target=target, resistance=natural + armor(runtime, state, binding.target_id)
    )


def prepare_delivery(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense | ResistComposedAttack,
    *,
    selected_attack: ComposedAttackChoice | None = None,
) -> tuple[PlayState, Encounter, ComposedDelivery]:
    pending = encounter.pending_defense
    if pending is None or pending.composed_attack_id is None:
        raise ValidationError("No approved composed attack awaits this response")
    binding = pending_binding(state.resources, encounter, pending)
    if command.actor_id != binding.target_id:
        raise ValidationError("Only the target may choose its response")
    if encounter.blocked_reason or pending.attack_roll is not None:
        raise ConflictError("The recorded critical requires its typed continuation")
    if selected_attack is None:
        current = preflight_pending(runtime, state, encounter)
    else:
        if isinstance(command, ResistComposedAttack) != (selected_attack.malediction is not None):
            raise ValidationError(
                "Captured attack requires its matching ordinary or resistance response"
            )
        if selected_attack.current.source != binding.source:
            raise ConflictError("Selected attack no longer matches its captured source")
        original = selected_attack.original
        expected = score_attack(
            original, selected_attack.selected.dice, ranged=selected_attack.ranged
        )
        if selected_attack.selected != expected:
            raise ValidationError("Selected attack differs from its captured original context")
        current = _current_impact(runtime, state, binding, selected_attack.current)
    hp_before = next(p.current for p in state.resources.pools if p.id == "hp:" + binding.target_id)
    delivery = _delivery(
        runtime, state, encounter, binding, current, command, selected_attack=selected_attack
    )
    prepared = ComposedDelivery(
        command=command,
        binding=binding,
        current=delivery.current,
        hp_before=hp_before,
        attack_captured=selected_attack is not None,
        hit=delivery.hit,
        defended=delivery.defended,
        checks=delivery.checks,
        selected=delivery.selected,
        defense=delivery.defense,
        value=delivery.value,
        effects=delivery.effects,
        critical_table=delivery.critical_table,
        critical_id=delivery.critical_id,
    )
    return delivery.state, delivery.encounter, prepared


def owner_damage_arguments(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    delivery: ComposedDelivery,
) -> OwnerDamageArguments:
    """The exact approved damage inputs shared by immediate and paused delivery."""
    pending = encounter.pending_defense
    if pending is None or pending.id != delivery.binding.pending_id:
        raise ConflictError("Composed damage continuation lost its attack identity")
    binding, current, command = delivery.binding, delivery.current, delivery.command
    source, effects = current.source, delivery.effects
    hit, defended, checks = delivery.hit, delivery.defended, delivery.checks
    target_build = build(runtime, state, binding.target_id)
    assert target_build.statistics is not None
    channel = AttackChannel(
        id=source.id,
        definition_id=source.purchase_id,
        attacker_id=binding.attacker_id,
        target_id=binding.target_id,
        location_id=current.context.location_id,
        kind="damage",
        damage_type=source.damage_type,
        composed=True,
        distance_yards=current.context.distance_yards,
        attack_score=10,
        attack_roll=10 if hit else 18,
        defense_succeeded=defended,
        resolved_checks=checks,
        malediction_resolved=binding.stage == "resistance",
        malediction_resisted=isinstance(command, ResistComposedAttack)
        and command.resist
        and not hit,
        contagion_vector=source.contagion_vector,
        incubation_seconds=source.incubation_seconds,
    )
    defending_build = build(runtime, state, binding.target_id, defensive=True)
    assert defending_build.statistics is not None
    defender = next(p for p in encounter.participants if p.actor_id == binding.target_id)
    fitness = physical_traits(defending_build, runtime.reviewer.compiler.definitions).fitness
    cyclic_modifiers = check_modifiers(state.resources, binding.target_id, "ht", defensive=True)
    if fitness:
        cyclic_modifiers += (Modifier(fitness, "Fitness resistance", "B55", "characters-third"),)
    return OwnerDamageArguments(
        command=TraitAttackCommand(
            id=pending.id,
            actor_id=binding.attacker_id,
            expected_revision=state.resources.revision,
            definition_id=source.purchase_id,
            channel_id=source.id,
        ),
        attacker_build=current.attacker,
        target_build=current.target,
        channels=(channel,),
        target_ht=target_build.statistics.ht,
        consequences=TraitAttackConsequences(
            resistance=current.resistance,
            immune_to_damage=source.damage_type in ("tox", "fat")
            and bool(
                next(
                    p.injury and p.injury.machine
                    for p in state.resources.pools
                    if p.id == "hp:" + binding.target_id
                )
            ),
            maximum_damage=effects.maximum_damage,
            basic_multiplier=effects.basic_multiplier,
            halve_dr=effects.halve_dr,
            force_major_wound=effects.force_major_wound,
            double_shock=effects.double_shock,
            held_item_ids=tuple(dict.fromkeys(i for i, _ in defender.hand_bindings)),
            held_item_locations=defender.hand_bindings,
            cyclic_resistance_ht=defending_build.statistics.ht,
            cyclic_resistance_modifiers=cyclic_modifiers,
        ),
    )


def finish_delivery(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    delivery: ComposedDelivery,
    *,
    prepared_damage: PreparedOwnerDamage | None = None,
    selected_damage: tuple[int, ...] | None = None,
) -> ResolvedAttack:
    """Apply one captured delivery's consequences without repeating attack or defense."""
    pending = encounter.pending_defense
    if pending is None or pending.id != delivery.binding.pending_id:
        raise ConflictError("Composed delivery is no longer pending")
    binding, current, command = delivery.binding, delivery.current, delivery.command
    source = current.source
    hp_before = delivery.hp_before
    attack, defense, value = delivery.checks[0], delivery.defense, delivery.value
    effects, selected = delivery.effects, delivery.selected
    critical_table, critical_id = delivery.critical_table, delivery.critical_id
    target_build = build(runtime, state, binding.target_id)
    assert target_build.statistics is not None
    arguments = owner_damage_arguments(runtime, state, encounter, delivery)
    resources, outcome = apply_trait_attack(
        state.resources,
        state.world,
        arguments.command,
        arguments.attacker_build,
        arguments.target_build,
        runtime.reviewer.compiler.definitions,
        arguments.channels,
        target_ht=arguments.target_ht,
        rng=runtime.rng,
        authorized_actor_id=binding.attacker_id,
        system=True,
        consequences=arguments.consequences,
        prepared_damage=prepared_damage,
        selected_damage=selected_damage,
    )
    if outcome.injury and outcome.injury.dropped_ready_items:
        resources, encounter, _ = drop_held_items(
            resources,
            encounter,
            binding.target_id,
            outcome.injury.dropped_ready_items,
            location_id=current.context.location_id,
        )
    if effects.drop_all_held:
        resources, encounter, _ = drop_all_held(
            resources, encounter, binding.target_id, location_id=current.context.location_id
        )
    if source.damage_type == "fat" and effects.force_major_wound and outcome.fatigue is not None:
        dr = effective_dr(
            current.resistance, source.profile.armor_divisor, location="torso", damage_type="fat"
        )
        basic = (
            6 * source.damage_dice if effects.maximum_damage else sum(outcome.damage_dice)
        ) * effects.basic_multiplier
        if current.context.distance_yards >= source.profile.half_damage_range:
            basic //= 2
        prior_major = (
            next(
                (c.check for c in outcome.fatigue.injury.checks if c.reason == "major-wound"), None
            )
            if outcome.fatigue.injury
            else None
        )
        resources, encounter, _ = apply_fatigue_major_wound(
            resources,
            encounter,
            binding.target_id,
            pending.id,
            already_checked=prior_major is not None,
            prior_check=prior_major,
            penetration=max(0, basic - dr),
            ht=target_build.statistics.ht,
            rng=runtime.rng,
            location_id=current.context.location_id,
            system=True,
        )
    occurrence = _id(pending.id, "cyclic")
    if any(c.id == occurrence for c in resources.cyclic_attacks):
        if source.cyclic_policy is None:
            raise ValidationError("Delivered Cyclic attack has no approved stopping policy")
        resources = bind_occurrence(
            resources,
            occurrence,
            source_id=source.id,
            source_revision=source.source_revision,
            policy=source.cyclic_policy,
            bypass_dr=binding.stage == "resistance",
        )
    state = state.model_copy(update={"resources": resources})
    hp = next(p for p in resources.pools if p.id == "hp:" + binding.target_id)
    assert hp.injury is not None
    target = next(p for p in encounter.participants if p.actor_id == binding.target_id)
    encounter = CombatEngine._replace(
        encounter,
        target.model_copy(update={"posture": "prone" if hp.injury.prone else target.posture}),
    )
    encounter = distracted(
        runtime,
        state,
        encounter,
        binding.target_id,
        defended=defense is not None,
        injured=hp.current < hp_before,
    )
    injury = outcome.injury
    basic = (
        6 * source.damage_dice if effects.maximum_damage else sum(outcome.damage_dice)
    ) * effects.basic_multiplier
    if (
        binding.stage != "resistance"
        and current.context.distance_yards >= source.profile.half_damage_range
    ):
        basic //= 2
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        attack_value=DerivedValue(
            "attack:innate-" + source.specialty, Decimal(attack.effective_target), ()
        ),
        defense_value=value,
        damage_dice=outcome.damage_dice,
        basic_damage=basic,
        resistance=injury.effective_resistance if injury else current.resistance,
        injury=hp_before - hp.current,
        hp_before=hp_before,
        hp_after=hp.current,
        incapacitated=hp.injury.incapacitated,
        profile_id=PROFILE,
        rules_version=attack.rules_version,
        critical_table=critical_table,
        adjudication_required=encounter.blocked_reason,
    )
    record = ComposedResolution(
        command_id=command.id,
        binding_id=binding.id,
        selected=selected,
        outcome=outcome,
        trace=trace,
        critical_id=critical_id,
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id=identity(RESOLUTION_PREFIX, command.id),
                            at=resources.game_time,
                            target_id=binding.target_id,
                            kind=record.model_dump_json(),
                        ),
                    )
                }
            )
        }
    )
    if encounter.blocked_reason:
        encounter = encounter.model_copy(
            update={"pending_defense": pending.model_copy(update={"attack_roll": attack})}
        )
        result = CombatResult(
            encounter_id=encounter.id,
            code="combat.resolved",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            injury=trace,
            available=(),
        )
    else:
        state, encounter, result = finish(
            runtime,
            state,
            encounter,
            binding,
            selected,
            command.id,
            trace,
            captured_attacker=current.attacker
            if prepared_damage is not None or delivery.attack_captured
            else None,
        )
    return ResolvedAttack(state, encounter, result, outcome)
