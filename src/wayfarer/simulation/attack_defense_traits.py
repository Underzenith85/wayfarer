"""Attack/defense trait adapter over the canonical injury and resource reducers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal

from pydantic import Field

from wayfarer.character.attack_defense_traits import AttackDefenseTraits, attack_defense_traits
from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.rules.catalog import RuleDefinition
from wayfarer.rules.checks import RandomSource
from wayfarer.simulation.gurps_equipment import DamageType
from wayfarer.simulation.injury import InjuryResult, Wound, apply_injury
from wayfarer.simulation.resources import Command, ResourceEvent, ResourceState, Scheduled
from wayfarer.world import World

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


class TraitAttackCommand(Command):
    definition_id: str
    channel_id: str


class TraitAttackOutcome(Record):
    outcome: Literal["injured", "applied", "resisted"]
    attacker_id: str
    target_id: str
    definition_id: str
    injury: InjuryResult | None = None
    effect_id: str | None = None
    expires_at: int | None = Field(default=None, ge=0)
    healed: int = Field(default=0, ge=0)


class TraitAttackEvent(Record):
    command_id: str
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


def _heal_attacker(
    resources: ResourceState, attacker_id: str, injury: int, *, enabled: bool
) -> tuple[ResourceState, int]:
    if not enabled or injury < 3:
        return resources, 0
    amount = injury // 3
    pool_id = "hp:" + attacker_id
    pool = next((value for value in resources.pools if value.id == pool_id), None)
    if pool is None:
        raise ValidationError("Vampiric Bite requires the attacker's canonical HP pool")
    healed = min(amount, pool.maximum - pool.current)
    if healed == 0:
        return resources, 0
    return (
        resources.model_copy(
            update={
                "pools": tuple(
                    value.model_copy(update={"current": value.current + healed})
                    if value.id == pool_id
                    else value
                    for value in resources.pools
                )
            }
        ),
        healed,
    )


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
) -> tuple[ResourceState, TraitAttackOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Trait attack requires attacker authority")
    previous = next((event for event in history(resources) if event.command_id == command.id), None)
    if previous is not None:
        if previous.channel_id == command.channel_id:
            return resources, previous.outcome
        raise ConflictError("Trait attack command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Trait attack revision changed")
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.definition_id != command.definition_id
        or channel.attacker_id != command.actor_id
        or channel.kind != KINDS.get(command.definition_id)
        or channel.attacker_id == channel.target_id
    ):
        raise ValidationError("Authored trait attack channel is unavailable")
    attacker = attack_defense_traits(attacker_build, definitions)
    target = attack_defense_traits(target_build, definitions)
    if attacker.purchase(command.definition_id) is None:
        raise ValidationError("Attack trait is not in the approved build")
    entities = {entity.id: entity for entity in world.entities}
    if (
        channel.attacker_id not in entities
        or channel.target_id not in entities
        or channel.location_id not in entities
        or entities[channel.attacker_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
    ):
        raise ValidationError("Trait attack context changed")

    if channel.kind == "damage":
        if channel.basic_damage < 1 or channel.resistance_score is not None:
            raise ValidationError("Damaging trait attack requires positive authored damage")
        if attacker.natural_damage_type(command.definition_id) != channel.damage_type:
            raise ValidationError("Damage type differs from the approved natural attack")
        multiplier = target.injury_multiplier("very-common")
        damage = channel.basic_damage * multiplier
        if (
            attacker.purchase("disadvantage:weak-bite") is not None
            and command.definition_id == "advantage:teeth"
        ):
            damage = max(0, damage - 1)
        state, result = apply_injury(
            _bind_target_tolerance(resources, channel.target_id, target),
            Wound(
                id=_id(command.id, "injury"),
                actor_id=channel.target_id,
                expected_revision=resources.revision,
                basic_damage=damage,
                resistance=target.damage_resistance(),
                damage_type=channel.damage_type,
                armor_divisor=channel.armor_divisor,
            ),
            ht=target_ht,
            rng=rng,
            system=True,
        )
        state, healed = _heal_attacker(
            state,
            channel.attacker_id,
            result.injury,
            enabled=command.definition_id == "advantage:vampiric-bite",
        )
        state = _apply_survival_traits(state, channel.target_id, target)
        outcome = TraitAttackOutcome(
            outcome="injured",
            attacker_id=channel.attacker_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            injury=result,
            healed=healed,
        )
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
        update: dict[str, object] = {"revision": resources.revision + 1}
        if not resisted:
            update["active_effect_ids"] = tuple(
                sorted(set(resources.active_effect_ids) | {effect_id})
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
        state = resources.model_copy(update=update)

    event = TraitAttackEvent(command_id=command.id, channel_id=channel.id, outcome=outcome)
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
