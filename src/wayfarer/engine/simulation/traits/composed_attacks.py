"""Approved Innate Attack composition over existing modifier, roll and injury reducers."""

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.character.traits.sensory import sensory_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import Contestant, resistance_roll, success_roll
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.traits.cyclic import cyclic_profile
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.rules.types.cyclic import require_cyclic_settled
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.symptom_state import active, acute_blindness, projected_build
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.attack_defense import (
    AttackChannel,
    TraitAttackCommand,
    TraitAttackOutcome,
    _channel,
    apply_trait_attack,
    history,
)
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PROFILE = "gurps-basic-set-4e-2004"


class AttackCompositionContext(Record):
    """Trusted scene/gear facts; these are not player-supplied command modifiers."""

    channel_id: str = "attack"
    target_id: str
    location_id: str
    distance_yards: int = Field(ge=0)
    maneuver: Literal["attack", "concentrate"] = "attack"
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    incubation_seconds: int = Field(default=86400, ge=1, le=31536000)
    specialty: Literal["beam", "breath", "gaze", "projectile"] = "projectile"
    aim_seconds: int = Field(default=0, ge=0, le=3)
    defense: Literal["none", "dodge"] = "dodge"
    defender_attack_awareness: str | None = Field(
        default=None, min_length=1, max_length=2000, pattern=r"\S"
    )
    target_perceived: bool = True
    vision_contact: bool = True
    resist: bool = True


def _ordinary_check(
    state: ResourceState,
    actor_id: str,
    build: ValidatedBuild,
    profile: AttackProfile,
    context: AttackCompositionContext,
    rng: RandomSource,
) -> CheckTrace:
    assert build.statistics is not None
    identifier = "skill:innate-attack-" + context.specialty
    purchased = {
        v.target: int(v.value)
        for v in build.sheet.values
        if v.target.startswith("skill:innate-attack-")
    }
    target = purchased.get(
        identifier, max([build.statistics.dx - 4] + [value - 2 for value in purchased.values()])
    )
    target += range_penalty(context.distance_yards)
    if context.aim_seconds:
        target += profile.accuracy + max(0, context.aim_seconds - 1)
    if target < 3:
        raise ValidationError("Composed attack effective skill is below three")
    return success_roll(PROFILE, target, modifiers=check_modifiers(state, actor_id, "dx"), rng=rng)


def _malediction_checks(
    state: ResourceState,
    actor_id: str,
    build: ValidatedBuild,
    target: ValidatedBuild,
    profile: AttackProfile,
    context: AttackCompositionContext,
    definitions: Mapping[str, RuleDefinition],
    rng: RandomSource,
) -> tuple[bool, tuple[CheckTrace, ...]]:
    assert build.statistics is not None and target.statistics is not None
    if context.maneuver != "concentrate":
        raise ValidationError("Malediction requires Concentrate")
    if not context.target_perceived:
        raise ValidationError("Malediction requires a clearly perceived target")
    if profile.penetration_sense == "vision" and (
        not context.vision_contact
        or any(
            e.spec.kind == "blindness"
            for actor in (actor_id, context.target_id)
            for e in active(state, actor)
        )
    ):
        raise ValidationError("Vision-Based Malediction requires the victim's available vision")
    penalties = (
        Modifier(-context.distance_yards, "Malediction 1 range", "B106", "characters-third"),
    )
    if not context.resist:
        check = success_roll(PROFILE, build.statistics.will, modifiers=penalties, rng=rng)
        return check.outcome.succeeded, (check,)
    resistance = resistance_roll(
        PROFILE,
        Contestant(actor_id, build.statistics.will, penalties),
        Contestant(
            context.target_id,
            target.statistics.will,
            (
                (Modifier(5, "Protected vision", "B78/B109", "characters-third"),)
                if profile.penetration_sense == "vision"
                and sensory_traits(target, definitions).protected("vision")
                else ()
            ),
        ),
        rule_of_16=True,
        rng=rng,
    )
    return resistance.affected, (resistance.attacker, resistance.resister)


def _resolve_delivery(
    state: ResourceState,
    actor_id: str,
    build: ValidatedBuild,
    target: ValidatedBuild,
    profile: AttackProfile,
    context: AttackCompositionContext,
    definitions: Mapping[str, RuleDefinition],
    rng: RandomSource,
) -> tuple[bool, bool, tuple[CheckTrace, ...]]:
    if profile.malediction_range != "none":
        hit, checks = _malediction_checks(
            state, actor_id, build, target, profile, context, definitions, rng
        )
        return hit, False, checks
    if context.maneuver != "attack" or not context.target_perceived:
        raise ValidationError(
            "Ordinary composed attack requires a supported perceived target and Attack maneuver"
        )
    if any(e.spec.kind == "blindness" for e in active(state, actor_id)):
        raise ValidationError(
            "Blind ordinary composed attacks require a separate supported consumer"
        )
    if context.distance_yards > profile.max_range:
        raise ValidationError("Target exceeds the approved composed attack range")
    blind_defender = acute_blindness(state, context.target_id)
    if blind_defender and context.defense == "dodge" and context.defender_attack_awareness is None:
        raise ValidationError("Blind composed defense requires independent attack awareness")
    check = _ordinary_check(state, actor_id, build, profile, context, rng)
    defended = False
    checks = (check,)
    if (
        check.outcome.succeeded
        and check.outcome is not Outcome.CRITICAL_SUCCESS
        and context.defense == "dodge"
    ):
        assert target.statistics is not None
        defense = success_roll(
            PROFILE, target.statistics.dodge - (4 if blind_defender else 0), rng=rng
        )
        checks += (defense,)
        defended = defense.outcome.succeeded
    return check.outcome.succeeded, defended, checks


def apply_composed_attack(
    state: ResourceState,
    world: World,
    command: TraitAttackCommand,
    attacker_build: ValidatedBuild,
    target_build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    context: AttackCompositionContext,
    *,
    rng: RandomSource,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, TraitAttackOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Composed attack requires attacker authority")
    if command.definition_id != "advantage:innate-attack":
        raise ValidationError("Composed attack requires an approved Innate Attack")
    traits = attack_defense_traits(attacker_build, definitions)
    purchase = next(
        (p for p in attacker_build.trait_purchases if p.definition_id == command.definition_id),
        None,
    )
    kind = traits.natural_damage_type(command.definition_id)
    if purchase is None or purchase.trait is None or kind is None:
        raise ValidationError("Composed attack is not in the approved character")
    channel = AttackChannel.model_validate(
        {
            "id": context.channel_id,
            "definition_id": command.definition_id,
            "attacker_id": command.actor_id,
            "target_id": context.target_id,
            "location_id": context.location_id,
            "kind": "damage",
            "damage_type": kind,
            "composed": True,
            "distance_yards": context.distance_yards,
            "contagion_vector": context.contagion_vector,
            "incubation_seconds": context.incubation_seconds,
        }
    )
    if any(event.command_id == command.id for event in history(state)):
        return apply_trait_attack(
            state,
            world,
            command,
            attacker_build,
            target_build,
            definitions,
            (channel,),
            target_ht=10,
            rng=rng,
            authorized_actor_id=authorized_actor_id,
            system=system,
        )
    if state.revision != command.expected_revision:
        raise ConflictError("Composed attack revision changed")
    require_cyclic_settled(state.cyclic_attacks, state.game_time + 1)
    _channel(world, command, (channel,))
    profile = cyclic_profile(purchase.trait.attack_modifiers, kind)
    attacker = projected_build(state, command.actor_id, attacker_build, definitions)
    target = projected_build(state, context.target_id, target_build, definitions)
    if (
        attacker.statistics is None
        or target.statistics is None
        or attacker.statistics.profile_id != PROFILE
        or target.statistics.profile_id != PROFILE
    ):
        raise ValidationError("Composed attack requires approved Basic Set statistics")
    hit, defended, checks = _resolve_delivery(
        state, command.actor_id, attacker, target, profile, context, definitions, rng
    )
    channel = channel.model_copy(
        update={
            "attack_score": 10,
            "attack_roll": 10 if hit else 18,
            "defense_succeeded": defended,
            "resolved_checks": checks,
            "malediction_resolved": profile.malediction_range != "none",
            "malediction_resisted": profile.malediction_range != "none"
            and context.resist
            and not hit,
        }
    )
    return apply_trait_attack(
        state,
        world,
        command,
        attacker_build,
        target_build,
        definitions,
        (channel,),
        target_ht=target.statistics.ht,
        rng=rng,
        authorized_actor_id=authorized_actor_id,
        system=system,
    )
