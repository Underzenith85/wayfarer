"""Preparing the shot the defender must answer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.close_combat import (
    opponents_in_close_combat,
    pair,
    stray_target_order,
)
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.equipment_entry import weapon_target
from wayfarer.engine.simulation.combat.firearm_transitions import validate_attack
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.objects.combat import target_geometry
from wayfarer.engine.simulation.combat.objects.locations import validate_target
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.ranged.special import validate_cover_geometry
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.spatial import point_distance
from wayfarer.engine.simulation.combat.tactical import defense_adjustment
from wayfarer.engine.simulation.combat.thrown.explosions import separation, validate_position
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.visibility import combat_visibility
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def _validate_penetration_targets(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    attacker_id: str,
    target_id: str,
    weapon_item_id: str,
    target_item_id: str | None,
    cover_item_id: str | None,
    overpenetration_target_id: str | None,
) -> None:
    penetrating = weapon.damage.damage_type in ("imp", "pi-", "pi", "pi+", "pi++") or (
        weapon.damage.damage_type == "burn" and weapon.damage.tight_beam
    )
    if cover_item_id is not None:
        if target_item_id is not None or cover_item_id == weapon_item_id:
            raise ValidationError("Cover cannot also be the attack target or attacking weapon")
        validate_cover_geometry(
            runtime,
            state,
            encounter,
            attacker_id=attacker_id,
            target_id=target_id,
            barrier_item_id=cover_item_id,
        )
        if not penetrating:
            raise ValidationError("Selected attack cannot pass through cover")
    if overpenetration_target_id is None:
        return
    if overpenetration_target_id in (attacker_id, target_id):
        raise ValidationError("Overpenetration requires a distinct secondary target")
    secondary = next(
        (p for p in encounter.participants if p.actor_id == overpenetration_target_id), None
    )
    if secondary is None:
        raise ValidationError("Unknown overpenetration target")
    actor = next(p for p in encounter.participants if p.actor_id == attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == target_id)
    if point_distance(actor.position, target.position) + point_distance(
        target.position, secondary.position
    ) != point_distance(actor.position, secondary.position):
        raise ValidationError("Overpenetration target is not behind the primary target")
    if not penetrating:
        raise ValidationError("Selected attack cannot overpenetrate")


def _area_attack_distance(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    weapon_item_id: str,
    actor_id: str,
    aim_point: GroundPosition | None,
    scatter_squared: bool,
    shots: int,
    hit_location: HitLocation | None,
    target_item_id: str | None,
    cover_item_id: str | None,
    overpenetration_target_id: str | None,
) -> float | None:
    if scatter_squared and aim_point is None:
        raise ValidationError("Squared scatter requires a declared area aim point")
    if aim_point is None:
        return None
    if target_item_id or cover_item_id or overpenetration_target_id or hit_location:
        raise ValidationError("Area aim cannot select a body part, object, or penetration target")
    if shots != 1:
        raise ValidationError("An area attack resolves one payload at a time")
    source = next(i for i in state.resources.items if i.id == weapon_item_id)
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    ammunition = entries.get(weapon.ammunition_id or "")
    if entries[source.definition_id].warhead is None and (
        ammunition is None or ammunition.warhead is None
    ):
        raise ValidationError("Area aim requires an explosive or area-effect payload")
    validate_position(runtime, encounter, aim_point)
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    return float(separation(position(encounter, actor), aim_point))


def prepare(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    shots: int,
    hit_location: HitLocation | None,
    target_item_id: str | None = None,
    cover_item_id: str | None = None,
    overpenetration_target_id: str | None = None,
    area_aim_point: GroundPosition | None = None,
    scatter_squared: bool = False,
) -> Encounter:

    pending = encounter.pending_defense
    assert pending is not None
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    area_distance = _area_attack_distance(
        runtime,
        state,
        encounter,
        weapon,
        weapon_item_id=pending.weapon_id,
        actor_id=pending.attacker_id,
        aim_point=area_aim_point,
        scatter_squared=scatter_squared,
        shots=shots,
        hit_location=hit_location,
        target_item_id=target_item_id,
        cover_item_id=cover_item_id,
        overpenetration_target_id=overpenetration_target_id,
    )
    visibility = combat_visibility(encounter, actor.actor_id, target.actor_id)
    close = bool(opponents_in_close_combat(encounter, actor.actor_id))
    defender_close = bool(opponents_in_close_combat(encounter, target.actor_id))
    bystanders = tuple(
        participant.actor_id
        for participant in encounter.participants
        if area_aim_point is None
        if participant.actor_id not in (actor.actor_id, target.actor_id)
        and pair(participant.actor_id, target.actor_id) in encounter.close_pairs
    )
    selectors = draw_dice(runtime.rng, len(bystanders)) if bystanders else ()
    stray_order = stray_target_order(bystanders, selectors) if bystanders else ()
    geometry = encounter
    if target_item_id:
        geometry = target_geometry(runtime, state, encounter, target_item_id)
    scene = situation(
        runtime,
        geometry,
        actor.actor_id,
        target.actor_id,
        weapon,
        ground=bool(
            target_item_id
            and next(i for i in state.resources.items if i.id == target_item_id).ground
        ),
    )
    attack_distance = area_distance if area_distance is not None else scene.distance

    if len(disabled(state.resources, actor.actor_id) & {"left-eye", "right-eye"}) == 2:
        raise ValidationError("Blind ranged attacks require an explicit sensory targeting adapter")
    stats = build(runtime, state, actor.actor_id).statistics
    assert stats is not None
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    validate_rated_strength(catalog(runtime).profile_id, weapon, st)
    range_st = weapon.rated_strength.st if weapon.rated_strength is not None else st
    if attack_distance > float(weapon.maximum_range) * (
        range_st if weapon.range_basis == "st" else 1
    ):
        raise ValidationError("Target exceeds maximum ranged weapon range")
    if shots > weapon.rate_of_fire or (
        shots > 1 and catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Unsupported fire mode or shot count for profile")

    validate_target(
        runtime, state, encounter, actor.actor_id, target.actor_id, weapon, hit_location
    )
    _validate_penetration_targets(
        runtime,
        state,
        encounter,
        weapon,
        attacker_id=actor.actor_id,
        target_id=target.actor_id,
        weapon_item_id=pending.weapon_id,
        target_item_id=target_item_id,
        cover_item_id=cover_item_id,
        overpenetration_target_id=overpenetration_target_id,
    )
    if actor.last_maneuver == "feint" or (
        actor.last_maneuver == "all_out_attack"
        and actor.maneuver_state.attack_bonus != 4
        and pending.suppression_zone_id is None
    ):
        raise ValidationError("Ranged All-Out Attack supports Determined only")
    item = next(i for i in state.resources.items if i.id == pending.weapon_id)

    if weapon.firearm is not None and catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Firearm malfunctions require the exact Basic Set profile")
    if pending.suppression_zone_id is None:
        validate_attack(state.resources, item.id, weapon, shots)
    if item.quantity != 1:
        raise ValidationError("Ranged weapon requires an individual inventory item")
    if not weapon.thrown and pending.suppression_zone_id is None:
        load = next(
            (loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id == item.id),
            None,
        )
        needed = shots if weapon.sprayer is None else weapon.sprayer.rounds_per_second
        if (
            load is None
            or load.mode_id != weapon.id
            or load.rounds < needed
            or (load.reload_progress and weapon.reload_protocol != "per-round")
            or (
                load.readiness is not None
                and load.readiness.stage not in ("loaded", "unload")
                and weapon.reload_protocol != "per-round"
            )
        ):
            raise ValidationError("Weapon is unloaded or reload is incomplete")
        if shots < min(weapon.minimum_shots_per_attack, load.rounds):
            raise ValidationError("Declared burst is below the automatic-only minimum")
    if weapon.sprayer is not None:
        # B205: a stream is held, second by second, until the firer stops or the
        # projector's own sustained-seconds ceiling is reached (#359).
        held = actor.stream
        if (
            held is not None
            and (held.weapon_id, held.mode_id) == (item.id, weapon.id)
            and held.exhausted
        ):
            raise ValidationError("This stream has run for as long as it can be held")
        if weapon.sprayer.ignites and encounter.scene_id is None:
            raise ValidationError("Lingering fire requires an authoritative encounter scene")
    allowed: list[Defense] = ["none"]
    # B178: a shot laid indirectly arrives without warning, so the target has no
    # active defense against it. A directly laid mount is defended normally.
    indirect = weapon.mount is not None and weapon.mount.indirect
    candidates = tuple(
        candidate
        for candidate in (
            () if indirect or area_aim_point is not None else ("dodge", "block", "parry")
        )
        if candidate in visibility.defenses
    )
    for candidate in candidates:
        if (
            target_item_id
            and next(i for i in state.resources.items if i.id == target_item_id).ground
        ):
            continue
        targeting_weapon = weapon_target(runtime, state, target_item_id)
        if targeting_weapon and candidate == "block":
            continue
        if candidate == "parry" and (
            not weapon.thrown or catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
        ):
            continue
        if candidate == "block" and (defender_close or not (weapon.thrown or weapon.blockable)):
            continue
        try:
            defense_adjustment(encounter, actor, target, approach=pending.tactical_approach)
            defense_value(
                runtime,
                state,
                target,
                candidate,
                target_item_id if targeting_weapon and candidate == "parry" else None,
            )
        except ValidationError:
            if candidate != "parry" or not weapon.catchable or targeting_weapon:
                continue

            try:
                unarmed_defense(runtime, state, encounter, target.actor_id, "parry", None)
            except ValidationError:
                continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "mode_id": weapon.id,
                    "shots": shots,
                    "allowed": tuple(allowed),
                    "hit_location": hit_location,
                    "target_item_id": target_item_id,
                    "cover_item_id": cover_item_id,
                    "overpenetration_target_id": overpenetration_target_id,
                    "area_aim_point": area_aim_point,
                    "scatter_squared": scatter_squared,
                    "visibility_attack_penalty": (
                        0 if area_aim_point is not None else visibility.attack_penalty
                    ),
                    "visibility_defense_penalty": visibility.defense_penalty,
                    "close_combat": close,
                    "defender_close_combat": defender_close,
                    "stray_target_order": stray_order,
                }
            )
        }
    )
