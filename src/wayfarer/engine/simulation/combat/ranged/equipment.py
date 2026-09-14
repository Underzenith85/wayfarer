"""Execution adapters for explicitly selected B275-B281 equipment facts."""

from decimal import Decimal

from wayfarer.engine.rules.types.affliction import AfflictionEffect
from wayfarer.engine.rules.types.ranged_equipment import (
    BackBlastEvent,
    FollowUpResult,
    FollowUpSpec,
)
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentCatalog,
    EquipmentProfile,
    RangedMode,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState, Scheduled
from wayfarer.errors import ValidationError


def ammunition_profile(
    catalog: EquipmentCatalog,
    resources: ResourceState,
    weapon_item_id: str,
    mode: RangedMode,
) -> EquipmentProfile | None:
    """Return the exact reserved ammunition choice for this weapon and mode."""

    load = next(
        (
            value
            for value in resources.ammunition_loads
            if value.weapon_id == weapon_item_id and value.mode_id == mode.id
        ),
        None,
    )
    if load is None:
        return None
    item = next((value for value in resources.items if value.id == load.ammunition_item_id), None)
    if item is None:
        raise ValidationError("Loaded ammunition source no longer exists")
    return next(value for value in catalog.entries if value.definition_id == item.definition_id)


def ammunition_matches(mode: RangedMode, profile: EquipmentProfile) -> bool:
    """Accept the ordinary load or an explicitly compatible typed variant."""

    variant = profile.ammunition_variant
    return profile.definition_id == mode.ammunition_id or (
        variant is not None and variant.base_definition_id == mode.ammunition_id
    )


def effective_mode(mode: RangedMode, ammunition: EquipmentProfile | None) -> RangedMode:
    """Apply only authored ammunition effects to a copy of a catalog mode."""

    variant = ammunition.ammunition_variant if ammunition is not None else None
    if variant is None:
        return mode
    if variant.base_definition_id != mode.ammunition_id:
        raise ValidationError("Loaded ammunition variant does not match this weapon")
    dice = mode.damage.dice
    adds = mode.damage.adds + variant.damage_add + variant.damage_add_per_die * (dice or 0)
    damage = mode.damage.model_copy(
        update={
            "damage_type": variant.damage_type or mode.damage.damage_type,
            "armor_divisor": variant.armor_divisor or mode.damage.armor_divisor,
            "adds": adds,
        }
    )
    ranges: dict[str, object] = {
        "damage": damage,
        "maximum_range": Decimal(mode.maximum_range) * variant.range_multiplier,
    }
    if mode.half_damage_range is not None:
        ranges["half_damage_range"] = Decimal(mode.half_damage_range) * variant.range_multiplier
    return mode.model_copy(update=ranges)


def holdout_modifier(mode: RangedMode) -> int:
    """B200/B278-B279: a weapon's printed Bulk modifies Holdout directly."""

    return mode.bulk


def resolve_follow_up(
    spec: FollowUpSpec,
    *,
    penetrated_damage: int,
    target_ht: int,
    resistance_dice: tuple[int, int, int],
    resistance_dr: int = 0,
) -> FollowUpResult:
    """Resolve a carrier payload without re-resolving its penetrating hit."""

    if spec.requires_penetration and penetrated_damage <= 0:
        return FollowUpResult(payload_id=spec.payload_id, applied=False, reason="carrier-stopped")
    target = (
        target_ht
        + spec.resistance_penalty
        + (0 if spec.requires_penetration else int(Decimal(resistance_dr) / spec.armor_divisor))
    )
    total = sum(resistance_dice)
    margin = target - total
    failed = total > target
    return FollowUpResult(
        payload_id=spec.payload_id,
        applied=failed,
        reason="failed-resistance" if failed else "resisted",
        resistance_target=target,
        resistance_total=total,
        margin=-margin if failed else margin,
        duration_seconds=(
            max(0, total - target) * spec.duration_minutes_per_margin * 60 if failed else 0
        ),
    )


def surge_disruption(*, surge: bool, target_is_electrical: bool, penetrating_damage: int) -> bool:
    """Expose the B105 surge transition predicate to object/electronics authority."""

    return surge and target_is_electrical and penetrating_damage > 0


def persist_follow_up(
    resources: ResourceState,
    *,
    event_id: str,
    target_actor_id: str,
    spec: FollowUpSpec,
    result: FollowUpResult,
) -> ResourceState:
    """Persist one resolved payload and apply its authored injury condition."""

    if any(event.id == event_id for event in resources.events):
        return resources
    pools = resources.pools
    if result.applied and spec.condition is not None:
        pools = tuple(
            pool.model_copy(
                update={
                    "injury": pool.injury.model_copy(
                        update={
                            "stunned": spec.condition == "stun" or pool.injury.stunned,
                            "unconscious": spec.condition == "unconsciousness"
                            or pool.injury.unconscious,
                        }
                    )
                }
            )
            if pool.id == f"hp:{target_actor_id}" and pool.injury is not None
            else pool
            for pool in pools
        )
    update: dict[str, object] = {
        "pools": pools,
        "events": resources.events
        + (
            ResourceEvent(
                id=event_id,
                at=resources.game_time,
                target_id=target_actor_id,
                kind=result.model_dump_json(),
            ),
        ),
    }
    if result.applied and spec.condition is not None and result.duration_seconds:
        effect_id = event_id + ":effect"
        expires = resources.game_time + result.duration_seconds
        update.update(
            {
                "active_effect_ids": tuple(sorted(set(resources.active_effect_ids) | {effect_id})),
                "afflictions": resources.afflictions
                + (
                    AfflictionEffect(
                        id=effect_id,
                        actor_id=target_actor_id,
                        source_id=spec.payload_id,
                        condition=spec.condition,
                        started_at=resources.game_time,
                        expires_at=expires,
                    ),
                ),
                "scheduled": resources.scheduled
                + (
                    Scheduled(
                        id=event_id + ":expiry",
                        due=expires,
                        kind="expire",
                        target_id=effect_id,
                    ),
                ),
            }
        )
    return resources.model_copy(update=update)


def persist_surge(
    resources: ResourceState,
    *,
    event_id: str,
    target_item_id: str,
    penetrating_damage: int,
    target_is_electrical: bool,
) -> ResourceState:
    """Disable a penetrated electrical target and retain an idempotent event."""

    if not surge_disruption(
        surge=True,
        target_is_electrical=target_is_electrical,
        penetrating_damage=penetrating_damage,
    ) or any(event.id == event_id for event in resources.events):
        return resources
    return resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={"condition": item.condition.model_copy(update={"disabled": True})}
                )
                if item.id == target_item_id and item.condition is not None
                else item
                for item in resources.items
            ),
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=target_item_id,
                    kind="surge-disruption-v1",
                ),
            ),
        }
    )


def persist_back_blast(
    resources: ResourceState,
    *,
    event_id: str,
    weapon_item_id: str,
    mode: RangedMode,
    shots_fired: int,
) -> ResourceState:
    """Record the authored rear hazard exactly once when a launcher discharges."""

    if (
        mode.back_blast is None
        or shots_fired < 1
        or any(event.id == event_id for event in resources.events)
    ):
        return resources
    result = BackBlastEvent(
        weapon_item_id=weapon_item_id,
        dice=mode.back_blast.dice,
        adds=mode.back_blast.adds,
        range_yards=mode.back_blast.range_yards,
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=weapon_item_id,
                    kind=result.model_dump_json(),
                ),
            )
        }
    )
