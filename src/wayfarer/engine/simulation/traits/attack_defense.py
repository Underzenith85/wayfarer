"""Attack/defense trait adapter over the canonical injury and resource reducers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import (
    AttackDefenseTraits,
    attack_defense_traits,
)
from wayfarer.engine.character.traits.physiology import PhysiologyTraits, physiology_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import CheckTrace, Modifier, RandomSource, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.traits.cyclic import cyclic_profile
from wayfarer.engine.rules.traits.modifiers import AttackProfile
from wayfarer.engine.rules.types.affliction import AfflictionCondition, AfflictionEffect
from wayfarer.engine.rules.types.cyclic import (
    CyclicAttack,
    ZeroDamageCyclicAttack,
    require_cyclic_settled,
)
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.combat.special_damage import (
    PenetrationContext,
    resolve_affliction_penetration,
)
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.cyclic import save as save_cyclic
from wayfarer.engine.simulation.health.fatigue import FatigueCost, FatigueResult, apply_fatigue
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.hit_locations import effective_dr
from wayfarer.engine.simulation.health.injury import InjuryResult, Wound, apply_injury
from wayfarer.engine.simulation.health.symptoms import reconcile_recovery
from wayfarer.engine.simulation.health.symptoms import register as register_symptoms
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState, Scheduled
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "attack-defense-use:"
AttackKind = Literal["damage", "affliction", "binding"]

KINDS: Mapping[str, AttackKind] = {
    "advantage:affliction": "affliction",
    "advantage:binding": "binding",
    "advantage:claws": "damage",
    "advantage:constriction-attack": "damage",
    "advantage:innate-attack": "damage",
    "advantage:spines": "damage",
    "advantage:striker": "damage",
    "advantage:teeth": "damage",
    "advantage:vampiric-bite": "damage",
}


@dataclass(frozen=True)
class TraitAttackConsequences:
    """Private host facts; never accepted as a player command or authored channel."""

    resistance: int
    immune_to_damage: bool = False
    maximum_damage: bool = False
    basic_multiplier: int = 1
    halve_dr: Literal["up", "down"] | None = None
    force_major_wound: bool = False
    double_shock: bool = False
    held_item_ids: tuple[str, ...] = ()
    held_item_locations: tuple[tuple[str, HumanLocation], ...] = ()
    cyclic_resistance_ht: int | None = None
    cyclic_resistance_modifiers: tuple[Modifier, ...] = ()


class AttackChannel(Record):
    id: str
    definition_id: str
    attacker_id: str
    target_id: str
    location_id: str
    kind: AttackKind
    basic_damage: int = Field(default=0, ge=0)
    damage_type: DamageType = "cr"
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    resistance_score: int | None = Field(default=None, ge=1)
    attack_score: int = Field(default=10, ge=1)
    attack_roll: int = Field(default=10, ge=3, le=18)
    resistance_roll: int | None = Field(default=None, ge=3, le=18)
    duration_seconds: int = Field(default=1, ge=1)
    effect_level: int = Field(default=1, ge=1)
    condition: AfflictionCondition = "stun"
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    incubation_seconds: int = Field(default=86400, ge=1, le=31536000)
    composed: bool = False
    distance_yards: float = Field(default=0, ge=0, allow_inf_nan=False)
    defense_succeeded: bool = False
    malediction_resolved: bool = False
    malediction_resisted: bool = False
    resolved_checks: tuple[CheckTrace, ...] = Field(default=(), exclude_if=lambda v: not v)
    penetration: PenetrationContext = Field(default_factory=PenetrationContext)


class TraitAttackCommand(Command):
    definition_id: str
    channel_id: str


class TraitAttackOutcome(Record):
    outcome: Literal["injured", "applied", "resisted", "missed", "defended", "unaffected"]
    attacker_id: str
    target_id: str
    definition_id: str
    injury: InjuryResult | None = None
    fatigue: FatigueResult | None = Field(default=None, exclude_if=lambda value: value is None)
    effect_id: str | None = None
    expires_at: int | None = Field(default=None, ge=0)
    healed: int = Field(default=0, ge=0)
    condition: AfflictionCondition | None = None
    resistance_target: int | None = None
    penetration_reason: str | None = None
    checks: tuple[CheckTrace, ...] = Field(default=(), exclude_if=lambda value: not value)
    damage_dice: tuple[int, ...] = Field(default=(), exclude_if=lambda value: not value)
    modifier_profile: AttackProfile | None = Field(default=None, exclude_if=lambda v: v is None)


class TraitAttackEvent(Record):
    command_id: str
    command_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
    channel_id: str
    outcome: TraitAttackOutcome


def _id(command_id: str, purpose: str = "event") -> str:
    return PREFIX + purpose + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[TraitAttackEvent, ...]:
    return tuple(
        TraitAttackEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX + "event:")
    )


def _resisted(channel: AttackChannel) -> bool:
    if channel.resistance_score is None:
        if channel.resistance_roll is not None:
            raise ValidationError("Resistance roll requires a resistance score")
        return False
    if channel.resistance_roll is None:
        raise ValidationError("Resistance score requires a resistance roll")
    return (
        channel.attack_score - channel.attack_roll
        <= channel.resistance_score - channel.resistance_roll
    )


_INCAPACITATING = frozenset(
    {
        "agony",
        "choking",
        "coma",
        "daze",
        "ecstasy",
        "hallucinating",
        "paralysis",
        "retching",
        "seizure",
        "sleep",
        "unconsciousness",
    }
)


def _require_affliction_condition(channel: AttackChannel, attacker: AttackDefenseTraits) -> None:
    purchased = attacker.parameter("advantage:affliction", "effect")
    compatible = (
        channel.condition == "stun"
        if purchased == "stun"
        else channel.condition == "attribute-penalty"
        if purchased == "attribute-penalty"
        else channel.condition in _INCAPACITATING
        if purchased == "incapacitation"
        else False
    )
    if not compatible:
        raise ValidationError("Affliction condition differs from the approved attack")


def _roll_succeeds(total: int, target: int) -> bool:
    """B343 success boundary for a stored 3d total (critical degree is immaterial here)."""
    return total <= 4 or (total <= target and total not in (17, 18))


def _heal_attacker(
    resources: ResourceState,
    attacker_id: str,
    injury: int,
    *,
    enabled: bool,
    physiology: PhysiologyTraits,
) -> tuple[ResourceState, int]:
    if not enabled or injury < 3:
        return resources, 0
    amount = injury // 3
    pool_id = "hp:" + attacker_id
    pool = next((value for value in resources.pools if value.id == pool_id), None)
    if pool is None:
        raise ValidationError("Vampiric Bite requires the attacker's canonical HP pool")
    restored, healed = restore_hp(resources, pool, amount, kind="steal-hp", physiology=physiology)
    if healed == 0:
        return resources, 0
    updated = resources.model_copy(
        update={
            "pools": tuple(restored if value.id == pool_id else value for value in resources.pools)
        }
    )
    return reconcile_recovery(resources, updated), healed


def _bind_target_tolerance(
    resources: ResourceState, target_id: str, target: AttackDefenseTraits
) -> ResourceState:
    tolerance = target.injury_tolerance_profile()
    if tolerance is None:
        return resources
    pool_id = "hp:" + target_id
    return resources.model_copy(
        update={
            "pools": tuple(
                value.model_copy(
                    update={"injury": value.injury.model_copy(update={"tolerance": tolerance})}
                )
                if value.id == pool_id and value.injury is not None
                else value
                for value in resources.pools
            )
        }
    )


def _apply_survival_traits(
    resources: ResourceState, target_id: str, target: AttackDefenseTraits
) -> ResourceState:
    pool_id = "hp:" + target_id
    pools = []
    for pool in resources.pools:
        if pool.id != pool_id or pool.injury is None:
            pools.append(pool)
            continue
        status = pool.injury
        if status.dead and target.death_thresholds_ignored() > 0:
            status = status.model_copy(
                update={"dead": False, "unconscious": True, "mortal_wound": False}
            )
        if target.fragility() == "unnatural" and pool.current <= -pool.maximum:
            status = status.model_copy(update={"dead": True, "unconscious": True})
        pools.append(pool.model_copy(update={"injury": status}))
    return resources.model_copy(update={"pools": tuple(pools)})


def _apply_affliction(
    resources: ResourceState,
    command: TraitAttackCommand,
    channel: AttackChannel,
    attacker: AttackDefenseTraits,
    target: AttackDefenseTraits,
    target_ht: int,
) -> tuple[ResourceState, TraitAttackOutcome]:
    _require_affliction_condition(channel, attacker)
    if channel.basic_damage or channel.resistance_roll is None:
        raise ValidationError("Affliction requires a resistance roll and no basic damage")
    penetration = resolve_affliction_penetration(target.damage_resistance(), channel.penetration)
    attack_hit = _roll_succeeds(channel.attack_roll, channel.attack_score)
    tolerance = target.injury_tolerance_profile()
    choking_immunity = channel.condition == "choking" and bool(tolerance and tolerance.no_neck)
    resistance_target = (
        target_ht
        - max(0, attacker.level("advantage:affliction") - 1)
        + penetration.dr_bonus
        + penetration.resistance_modifier
    )
    if channel.resistance_score is not None and channel.resistance_score != resistance_target:
        raise ValidationError("Affliction resistance target differs from trusted target facts")
    resisted = _roll_succeeds(channel.resistance_roll, resistance_target)
    applies = attack_hit and penetration.applies and not choking_immunity and not resisted
    effect_id = _id(channel.id, channel.kind)
    expires = resources.game_time + channel.duration_seconds
    result_kind: Literal["applied", "resisted", "missed", "unaffected"] = (
        "missed"
        if not attack_hit
        else "unaffected"
        if not penetration.applies or choking_immunity
        else "resisted"
        if resisted
        else "applied"
    )
    outcome = TraitAttackOutcome(
        outcome=result_kind,
        attacker_id=channel.attacker_id,
        target_id=channel.target_id,
        definition_id=command.definition_id,
        effect_id=effect_id if applies else None,
        expires_at=expires if applies else None,
        condition=channel.condition,
        resistance_target=resistance_target,
        penetration_reason=("injury-tolerance-no-neck" if choking_immunity else penetration.reason),
    )
    update: dict[str, object] = {"revision": resources.revision + 1}
    if applies:
        update["active_effect_ids"] = tuple(sorted(set(resources.active_effect_ids) | {effect_id}))
        update["afflictions"] = resources.afflictions + (
            AfflictionEffect(
                id=effect_id,
                actor_id=channel.target_id,
                source_id=command.definition_id,
                condition=channel.condition,
                started_at=resources.game_time,
                expires_at=expires,
                level=channel.effect_level,
            ),
        )
        update["scheduled"] = resources.scheduled + (
            Scheduled(
                id=_id(channel.id, "expiry"),
                due=expires,
                kind="expire",
                target_id=effect_id,
                amount=channel.effect_level,
            ),
        )
    return resources.model_copy(update=update), outcome


def _apply_fatigue_attack(
    resources: ResourceState,
    command: TraitAttackCommand,
    channel: AttackChannel,
    attacker: AttackDefenseTraits,
    target: AttackDefenseTraits,
    target_ht: int,
    rng: RandomSource,
    *,
    modifier_profile: AttackProfile | None,
    consequences: TraitAttackConsequences | None = None,
) -> tuple[ResourceState, TraitAttackOutcome]:
    """B61 fatigue damage uses DR, then the B426 canonical signed FP ledger."""
    if (
        command.definition_id != "advantage:innate-attack"
        or attacker.natural_damage_type(command.definition_id) != "fat"
        or (channel.basic_damage < 1 and not channel.composed)
        or channel.resistance_score is not None
    ):
        raise ValidationError("Fatigue damage requires an approved Fatigue Innate Attack")
    purchase = attacker.purchase(command.definition_id)
    approved_divisor = (
        channel.composed
        and modifier_profile is not None
        and channel.armor_divisor == modifier_profile.armor_divisor
    )
    if (
        purchase is None
        or purchase.modifiers
        or (channel.armor_divisor != 1 and not approved_divisor)
    ):
        raise ValidationError("Fatigue attack requires the modifier-free baseline channel")
    ResourceState.model_validate(resources)
    hp = next((pool for pool in resources.pools if pool.id == "hp:" + channel.target_id), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Fatigue attack requires canonical Basic Set target physiology")
    hit = _roll_succeeds(channel.attack_roll, channel.attack_score)
    state = resources.model_copy(update={"revision": resources.revision + 1})
    fatigue = None
    result_kind: Literal["injured", "missed", "unaffected"] = "unaffected"
    if not hit:
        result_kind = "missed"
    elif not hp.injury.machine:
        resistance = effective_dr(
            0
            if channel.malediction_resolved
            else consequences.resistance
            if consequences is not None
            else target.damage_resistance(),
            channel.armor_divisor,
            location="torso",
            damage_type="fat",
        )
        if consequences is not None and consequences.halve_dr:
            resistance = (resistance + int(consequences.halve_dr == "up")) // 2
        amount = max(0, channel.basic_damage - resistance) * target.injury_multiplier(
            "natural-attacks"
        )
        state, fatigue = apply_fatigue(
            resources,
            FatigueCost(
                id=_id(command.id, "fatigue"),
                actor_id=channel.target_id,
                expected_revision=resources.revision,
                amount=amount,
                attack_damage=True,
            ),
            ht=target_ht,
            rng=rng,
            system=True,
            double_shock=bool(consequences and consequences.double_shock),
        )
        if fatigue.hp_lost:
            state = _apply_survival_traits(state, channel.target_id, target)
        result_kind = "injured" if amount else "unaffected"
    return state, TraitAttackOutcome(
        outcome=result_kind,
        attacker_id=channel.attacker_id,
        target_id=channel.target_id,
        definition_id=command.definition_id,
        fatigue=fatigue,
    )


def _channel(
    world: World, command: TraitAttackCommand, channels: tuple[AttackChannel, ...]
) -> AttackChannel:
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.definition_id != command.definition_id
        or channel.attacker_id != command.actor_id
        or channel.kind != KINDS.get(command.definition_id)
        or channel.attacker_id == channel.target_id
    ):
        raise ValidationError("Authored trait attack channel is unavailable")
    entities = {entity.id: entity for entity in world.entities}
    if (
        channel.attacker_id not in entities
        or channel.target_id not in entities
        or channel.location_id not in entities
        or entities[channel.attacker_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
    ):
        raise ValidationError("Trait attack context changed")

    return channel


def _schedule_cyclic(
    state: ResourceState,
    command: TraitAttackCommand,
    channel: AttackChannel,
    cyclic: AttackProfile | None,
    target: AttackDefenseTraits,
    outcome: TraitAttackOutcome,
    target_ht: int,
    at: int,
    damage_dice: int,
    consequences: TraitAttackConsequences | None = None,
    *,
    delivered: bool,
) -> ResourceState:
    # B103 repeats a delivered nonimmune exposure even if DR absorbs all
    # initial damage or B378 rounds it to zero. Delivery is separate from debt.
    if cyclic is not None and cyclic.cyclic_interval_seconds is not None and delivered:
        assert (
            cyclic.cyclic_interval_seconds is not None and cyclic.cyclic_stop_condition is not None
        )
        attack_type = ZeroDamageCyclicAttack if channel.basic_damage == 0 else CyclicAttack
        state = save_cyclic(
            state,
            attack_type.model_validate(
                {
                    "id": _id(command.id, "cyclic"),
                    "attacker_id": channel.attacker_id,
                    "actor_id": channel.target_id,
                    "attack_id": channel.id,
                    "basic_damage": channel.basic_damage,
                    "damage_dice": damage_dice,
                    "symptom_spec": cyclic.symptom_spec,
                    "additional_symptoms": cyclic.additional_symptoms,
                    "symptom_source_id": channel.attacker_id + ":" + channel.id,
                    "contagious": cyclic.contagious,
                    "contagion_vector": channel.contagion_vector,
                    "incubation_seconds": channel.incubation_seconds,
                    "damage_type": channel.damage_type,
                    "resistance": 0
                    if cyclic.malediction_range != "none"
                    else consequences.resistance
                    if consequences is not None
                    else target.damage_resistance(),
                    "armor_divisor": channel.armor_divisor,
                    "vulnerability_multiplier": target.injury_multiplier("natural-attacks"),
                    "ht": target_ht,
                    "resistance_modifier": cyclic.resistance_modifier,
                    "interval": cyclic.cyclic_interval_seconds,
                    "remaining": cyclic.cyclic_cycles - 1,
                    "due": at + cyclic.cyclic_interval_seconds,
                    "stop_condition": cyclic.cyclic_stop_condition,
                    "hp_debt": outcome.injury.injury
                    if outcome.injury
                    else outcome.fatigue.hp_lost
                    if outcome.fatigue
                    else 0,
                    "fp_debt": outcome.fatigue.fp_lost if outcome.fatigue else 0,
                }
            ),
        )

    return state


def _register_symptoms(
    state: ResourceState,
    command: TraitAttackCommand,
    channel: AttackChannel,
    profile: AttackProfile | None,
    outcome: TraitAttackOutcome,
) -> ResourceState:
    if profile is None or profile.symptom_spec is None or outcome.outcome != "injured":
        return state
    fatigue = channel.damage_type == "fat"
    amount = (
        outcome.fatigue.fp_lost
        if outcome.fatigue
        else outcome.injury.injury
        if outcome.injury
        else 0
    )
    return register_symptoms(
        state,
        actor_id=channel.target_id,
        source_id=channel.attacker_id + ":" + channel.id,
        injury_id=_id(command.id, "fatigue" if fatigue else "injury"),
        amount=amount,
        pool_id=("fp:" if fatigue else "hp:") + channel.target_id,
        spec=profile.symptom_spec,
        additional_specs=profile.additional_symptoms,
        restriction_id=_id(command.id, "cyclic")
        if profile.cyclic_interval_seconds is not None
        else None,
    )


def _cyclic_check(
    cyclic: AttackProfile | None,
    channel: AttackChannel,
    target_ht: int,
    rng: RandomSource,
    consequences: TraitAttackConsequences | None = None,
) -> CheckTrace | None:
    if (
        cyclic is None
        or cyclic.resistance_modifier is None
        or not _roll_succeeds(channel.attack_roll, channel.attack_score)
    ):
        return None
    ht = (
        consequences.cyclic_resistance_ht
        if consequences and consequences.cyclic_resistance_ht is not None
        else target_ht
    )
    return success_roll(
        "gurps-basic-set-4e-2004",
        ht + cyclic.resistance_modifier,
        modifiers=consequences.cyclic_resistance_modifiers if consequences else (),
        rng=rng,
    )


def _modified_channel(profile: AttackProfile | None, channel: AttackChannel) -> AttackChannel:
    if (
        profile is not None
        and profile.contagious != "none"
        and (channel.damage_type != "tox" or channel.contagion_vector is None)
    ):
        raise ValidationError(
            "Contagious Cyclic requires a toxic attack with an authored illness vector"
        )
    if channel.malediction_resolved and (profile is None or profile.malediction_range == "none"):
        raise ValidationError("Unmodified attacks cannot bypass DR as Malediction")
    if profile is not None:
        if profile.malediction_range == "none" and channel.distance_yards > profile.max_range:
            raise ValidationError("Target exceeds the approved composed attack range")
        if profile.malediction_range != "none" and not channel.malediction_resolved:
            raise ValidationError("Malediction requires the composed resistance consumer")
        channel = channel.model_copy(update={"armor_divisor": profile.armor_divisor})
    return channel


def _roll_composed_damage(
    profile: AttackProfile | None,
    channel: AttackChannel,
    hit: bool,
    check: CheckTrace | None,
    levels: int,
    rng: RandomSource,
    consequences: TraitAttackConsequences | None = None,
) -> tuple[AttackChannel, tuple[int, ...]]:
    if (
        not channel.composed
        or not hit
        or channel.defense_succeeded
        or (check and check.outcome.succeeded)
    ):
        return channel, ()
    maximum = consequences is not None and consequences.maximum_damage
    dice = () if maximum else draw_dice(rng, levels)
    basic = (6 * levels if maximum else sum(dice)) * (
        consequences.basic_multiplier if consequences is not None else 1
    )
    profile = profile or AttackProfile(accuracy=3)
    # B378 includes the boundary itself and rounds down, even to zero.
    if profile.malediction_range == "none" and channel.distance_yards >= profile.half_damage_range:
        basic //= 2
    return channel.model_copy(update={"basic_damage": basic}), dice


def apply_trait_attack(
    resources: ResourceState,
    world: World,
    command: TraitAttackCommand,
    attacker_build: ValidatedBuild,
    target_build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    channels: tuple[AttackChannel, ...],
    *,
    target_ht: int,
    rng: RandomSource,
    authorized_actor_id: str,
    system: bool = False,
    consequences: TraitAttackConsequences | None = None,
) -> tuple[ResourceState, TraitAttackOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Trait attack requires attacker authority")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    previous = next((event for event in history(resources) if event.command_id == command.id), None)
    if previous is not None:
        if (
            previous.channel_id == command.channel_id
            and previous.outcome.definition_id == command.definition_id
            and previous.outcome.attacker_id == command.actor_id
            and (previous.command_digest is None or previous.command_digest == digest)
        ):
            return resources, previous.outcome
        raise ConflictError("Trait attack command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Trait attack revision changed")
    require_cyclic_settled(resources.cyclic_attacks, resources.game_time + 1)
    channel = _channel(world, command, channels)
    if consequences is not None and (not channel.composed or consequences.resistance < 0):
        raise ValidationError("Private consequences require a composed delivery")
    attacker = attack_defense_traits(attacker_build, definitions)
    target = attack_defense_traits(target_build, definitions)
    if attacker.purchase(command.definition_id) is None:
        raise ValidationError("Attack trait is not in the approved build")
    purchased = next(
        p for p in attacker_build.trait_purchases if p.definition_id == command.definition_id
    )
    selections = () if purchased.trait is None else purchased.trait.attack_modifiers
    cyclic = cyclic_profile(selections, channel.damage_type) if selections else None
    channel = _modified_channel(cyclic, channel)
    hit = _roll_succeeds(channel.attack_roll, channel.attack_score)
    immune = bool(consequences and consequences.immune_to_damage)
    check = (
        _cyclic_check(cyclic, channel, target_ht, rng, consequences)
        if hit and not channel.defense_succeeded and not immune
        else None
    )
    channel, damage_dice = _roll_composed_damage(
        cyclic, channel, hit and not immune, check, purchased.amount, rng, consequences
    )
    if (cyclic is not None or channel.composed) and (not hit or channel.defense_succeeded):
        state = resources.model_copy(update={"revision": resources.revision + 1})
        outcome = TraitAttackOutcome(
            outcome="defended"
            if channel.defense_succeeded
            else "resisted"
            if channel.malediction_resisted
            else "missed",
            attacker_id=channel.attacker_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
        )
    elif immune or check is not None and check.outcome.succeeded:
        state = resources.model_copy(update={"revision": resources.revision + 1})
        outcome = TraitAttackOutcome(
            outcome="unaffected" if immune else "resisted",
            attacker_id=channel.attacker_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
        )
    elif channel.kind == "damage" and channel.damage_type == "fat":
        state, outcome = _apply_fatigue_attack(
            resources,
            command,
            channel,
            attacker,
            target,
            target_ht,
            rng,
            modifier_profile=cyclic,
            consequences=consequences,
        )
    elif channel.kind == "damage":
        if (
            channel.basic_damage < 1 and not channel.composed
        ) or channel.resistance_score is not None:
            raise ValidationError("Damaging trait attack requires positive authored damage")
        if attacker.natural_damage_type(command.definition_id) != channel.damage_type:
            raise ValidationError("Damage type differs from the approved natural attack")
        if (
            attacker.purchase("disadvantage:weak-bite") is not None
            and command.definition_id == "advantage:teeth"
        ):
            channel = channel.model_copy(update={"basic_damage": max(0, channel.basic_damage - 1)})
        state, result = apply_injury(
            _bind_target_tolerance(resources, channel.target_id, target),
            Wound(
                id=_id(command.id, "injury"),
                actor_id=channel.target_id,
                expected_revision=resources.revision,
                basic_damage=channel.basic_damage,
                resistance=0
                if cyclic and cyclic.malediction_range != "none"
                else consequences.resistance
                if consequences is not None
                else target.damage_resistance(),
                damage_type=channel.damage_type,
                armor_divisor=channel.armor_divisor,
                vulnerability_multiplier=Decimal(target.injury_multiplier("natural-attacks")),
            ),
            ht=target_ht,
            rng=rng,
            system=True,
            held_item_ids=consequences.held_item_ids if consequences else (),
            held_item_locations=consequences.held_item_locations if consequences else (),
            force_major_wound=bool(consequences and consequences.force_major_wound),
            double_shock=bool(consequences and consequences.double_shock),
            halve_dr=consequences.halve_dr if consequences else None,
        )
        state, healed = _heal_attacker(
            state,
            channel.attacker_id,
            result.injury,
            enabled=command.definition_id == "advantage:vampiric-bite",
            physiology=physiology_traits(attacker_build, definitions),
        )
        state = _apply_survival_traits(state, channel.target_id, target)
        outcome = TraitAttackOutcome(
            outcome="injured" if channel.basic_damage else "unaffected",
            attacker_id=channel.attacker_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            injury=result,
            healed=healed,
        )
    elif channel.kind == "affliction":
        state, outcome = _apply_affliction(resources, command, channel, attacker, target, target_ht)
    else:
        resisted = _resisted(channel)
        effect_id = _id(channel.id, channel.kind)
        expires = resources.game_time + channel.duration_seconds
        outcome = TraitAttackOutcome(
            outcome="resisted" if resisted else "applied",
            attacker_id=channel.attacker_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            effect_id=None if resisted else effect_id,
            expires_at=None if resisted else expires,
        )
        binding_update: dict[str, object] = {"revision": resources.revision + 1}
        if not resisted:
            binding_update["active_effect_ids"] = tuple(
                sorted(set(resources.active_effect_ids) | {effect_id})
            )
            binding_update["scheduled"] = resources.scheduled + (
                Scheduled(
                    id=_id(channel.id, "expiry"),
                    due=expires,
                    kind="expire",
                    target_id=effect_id,
                    amount=channel.effect_level,
                ),
            )
        state = resources.model_copy(update=binding_update)

    outcome = outcome.model_copy(
        update={
            "checks": channel.resolved_checks + (() if check is None else (check,)),
            "damage_dice": damage_dice,
            "modifier_profile": cyclic if channel.composed else None,
        }
    )
    state = _schedule_cyclic(
        state,
        command,
        channel,
        cyclic,
        target,
        outcome,
        target_ht,
        resources.game_time,
        purchased.amount,
        consequences,
        # Canonical consequences exist for delivered damage including zero
        # penetration, and exclude misses, defenses, resistance and immunity.
        delivered=outcome.injury is not None or outcome.fatigue is not None,
    )

    state = _register_symptoms(state, command, channel, cyclic, outcome)
    event = TraitAttackEvent(
        command_id=command.id, command_digest=digest, channel_id=channel.id, outcome=outcome
    )
    state = state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=_id(command.id),
                    at=resources.game_time,
                    target_id=channel.target_id,
                    kind=event.model_dump_json(),
                ),
            )
        }
    )
    return state, outcome
