"""Special ranged state and one-projectile cover accounting (B407-B413)."""

from __future__ import annotations

import hashlib
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.rules.types.special_ranged import CoverImpact, GuidanceState
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry
from wayfarer.engine.simulation.combat.objects.combat import target_positions
from wayfarer.engine.simulation.combat.spatial import point_distance
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def structural_cover_dr(profile: ObjectProfile, armor_divisor: Decimal) -> int:
    """B408 cover DR, applying the attack's divisor after adding structural HP."""

    if profile.cover_kind is None:
        raise ValidationError("Intervening object lacks an explicit cover adapter")
    total = Decimal(profile.dr)
    if profile.cover_kind == "structural":
        total += Decimal(profile.hp) / 4
    effective = (total / armor_divisor).to_integral_value(rounding=ROUND_CEILING)
    return max(1 if profile.dr == 0 and armor_divisor < 1 else 0, int(effective))


def living_cover_dr(*, hp: int, armor_dr: int, armor_divisor: Decimal) -> int:
    """B408 two-sided worn armor plus HP for a living intervening target."""

    if hp < 1 or armor_dr < 0:
        raise ValidationError("Living cover requires positive HP and nonnegative armor DR")
    return int(
        (Decimal(hp + 2 * armor_dr) / armor_divisor).to_integral_value(rounding=ROUND_CEILING)
    )


def validate_cover_geometry(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    attacker_id: str,
    target_id: str,
    barrier_item_id: str,
) -> None:
    """Require a mapped barrier position on a shortest path to the target."""

    attacker = next((p for p in encounter.participants if p.actor_id == attacker_id), None)
    target = next((p for p in encounter.participants if p.actor_id == target_id), None)
    if attacker is None or target is None:
        raise ValidationError("Cover geometry requires encounter participants")
    positions = target_positions(runtime, state, encounter, barrier_item_id)
    if not any(
        point_distance(attacker.position, position) > 0
        and point_distance(position, target.position) > 0
        and point_distance(attacker.position, position) + point_distance(position, target.position)
        == point_distance(attacker.position, target.position)
        for position in positions
    ):
        raise ValidationError("Cover object is not between attacker and target")


def impact_cover(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    projectile_id: str,
    barrier_item_id: str,
    target_id: str,
    basic_damage: int,
    damage: Damage,
) -> tuple[PlayState, Encounter, CoverImpact]:
    """Damage a barrier once and return resistance for the same projectile transaction."""

    if basic_damage < 0 or damage.damage_type not in ("imp", "pi-", "pi", "pi+", "pi++", "burn"):
        raise ValidationError("Only penetrating ranged damage can pass through cover")
    if damage.damage_type == "burn" and not damage.tight_beam:
        raise ValidationError("Only tight-beam burning damage can overpenetrate")
    command_id = (
        "cover-impact:"
        + hashlib.sha256(
            f"{encounter.id}:{projectile_id}:{barrier_item_id}:{target_id}".encode()
        ).hexdigest()
    )
    event_id = f"cover:{command_id}"
    prior = next((event for event in state.resources.events if event.id == event_id), None)
    if prior is not None:
        record = CoverImpact.model_validate_json(prior.kind)
        if (
            record.projectile_id,
            record.barrier_item_id,
            record.target_id,
            record.basic_damage,
        ) != (projectile_id, barrier_item_id, target_id, basic_damage):
            raise ConflictError("Projectile cover transaction cannot be replaced")
        return state, encounter, record
    item = next((item for item in state.resources.items if item.id == barrier_item_id), None)
    if item is None or item.quantity != 1 or item.condition is None:
        raise ValidationError("Cover requires one initialized durable object")
    entry = effective_entry(runtime, item)
    if entry.durability is None:
        raise ValidationError("Cover requires an explicit durability profile")
    if target_id == item.owner_id and item.equipped:
        raise ValidationError("A target's worn item is armor, not an intervening object")
    cover = structural_cover_dr(entry.durability, damage.armor_divisor)
    resources, _ = apply_object(
        runtime.resources,
        state.resources,
        DamageObject(
            id=command_id,
            actor_id=next(
                p.actor_id
                for p in encounter.participants
                if encounter.pending_defense and p.actor_id == encounter.pending_defense.attacker_id
            ),
            expected_revision=state.resources.revision,
            item_id=barrier_item_id,
            basic_damage=basic_damage,
            damage_type=damage.damage_type,
            armor_divisor=damage.armor_divisor,
        ),
        system=True,
        rng=runtime.rng,
    )
    record = CoverImpact(
        projectile_id=projectile_id,
        barrier_item_id=barrier_item_id,
        target_id=target_id,
        basic_damage=basic_damage,
        cover_dr=cover,
        residual_damage=max(0, basic_damage - cover),
        object_command_id=command_id,
    )
    resources = resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=barrier_item_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )
    updated = state.model_copy(update={"resources": resources})
    # deferred: equipment_effects imports combat dispatch while this reducer must
    # reuse its authoritative encounter synchronization after object damage.
    from wayfarer.engine.simulation.combat.equipment_effects import synchronize

    return updated, synchronize(updated, encounter), record


def acquire_guidance(
    encounter: Encounter,
    weapon: RangedMode,
    *,
    projectile_id: str,
    weapon_id: str,
    operator_id: str,
    target_id: str,
    distance_yards: Decimal,
    lock: CheckTrace,
    designator_id: str | None = None,
) -> Encounter:
    """Persist a successful B412 lock without consuming or resolving the projectile."""

    spec = weapon.guidance
    if spec is None:
        raise ValidationError("Unsupported special weapon has no guidance adapter")
    if not lock.outcome.succeeded:
        raise ValidationError("Guidance lock failed")
    if distance_yards < 0 or distance_yards > Decimal(weapon.maximum_range):
        raise ValidationError("Guidance target is outside maximum range")
    if spec.kind == "semi-active" and designator_id is None:
        raise ValidationError("Semi-active homing requires a designator")
    if any(projectile.id == projectile_id for projectile in encounter.guided_projectiles):
        raise ConflictError("Guided projectile ID already exists")
    assert weapon.half_damage_range is not None
    projectile = GuidanceState(
        id=projectile_id,
        weapon_id=weapon_id,
        mode_id=weapon.id,
        operator_id=operator_id,
        target_id=target_id,
        kind=spec.kind,
        speed_yards_per_second=Decimal(weapon.half_damage_range),
        remaining_yards=distance_yards,
        remaining_endurance_yards=Decimal(weapon.maximum_range),
        accuracy=weapon.accuracy
        if spec.kind == "guided"
        else weapon.accuracy
        if lock.outcome.succeeded
        else 0,
        lock=lock,
        designator_id=designator_id,
    )
    return encounter.model_copy(
        update={"guided_projectiles": encounter.guided_projectiles + (projectile,)}
    )


def advance_guidance(
    encounter: Encounter,
    projectile_id: str,
    *,
    operator_continues: bool = True,
    target_visible: bool = True,
    designation_continues: bool = True,
    seeker_jammed: bool = False,
) -> Encounter:
    """Advance exactly one second, retaining any interruption as replayable state."""

    projectile = next((p for p in encounter.guided_projectiles if p.id == projectile_id), None)
    if projectile is None:
        raise ValidationError("Unknown guided projectile")
    if projectile.status in ("lost", "arrived"):
        raise ConflictError("Guided projectile is already settled")
    reason: (
        Literal["operator-interrupted", "target-lost", "designation-lost", "jammed", "out-of-range"]
        | None
    ) = None
    if projectile.kind == "guided" and not operator_continues:
        reason = "operator-interrupted"
    elif projectile.kind == "guided" and not target_visible:
        reason = "target-lost"
    elif projectile.kind == "semi-active" and not designation_continues:
        reason = "designation-lost"
    elif projectile.kind != "guided" and seeker_jammed:
        reason = "jammed"
    remaining = max(Decimal(0), projectile.remaining_yards - projectile.speed_yards_per_second)
    endurance = max(
        Decimal(0), projectile.remaining_endurance_yards - projectile.speed_yards_per_second
    )
    if reason is None and endurance == 0 and remaining > 0:
        reason = "out-of-range"
    update: dict[str, object] = {
        "remaining_yards": remaining,
        "remaining_endurance_yards": endurance,
        "status": "lost" if reason else "arrived" if remaining == 0 else "in-flight",
        "lost_reason": reason,
    }
    revised = projectile.model_copy(update=update)
    return encounter.model_copy(
        update={
            "guided_projectiles": tuple(
                revised if p.id == projectile_id else p for p in encounter.guided_projectiles
            )
        }
    )
