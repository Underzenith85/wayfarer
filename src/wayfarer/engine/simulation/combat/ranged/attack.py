"""Preparing the shot the defender must answer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.ranged.strength import validate_rated_strength
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def prepare(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    shots: int,
    hit_location: HitLocation | None,
    target_item_id: str | None = None,
) -> Encounter:
    from wayfarer.engine.simulation.actors import build, catalog
    from wayfarer.engine.simulation.combat.melee.defense import defense_value

    pending = encounter.pending_defense
    assert pending is not None
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    geometry = encounter
    if target_item_id:
        from wayfarer.engine.simulation.combat.objects.combat import target_geometry

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
    from wayfarer.engine.simulation.health.hit_locations import disabled

    if len(disabled(state.resources, actor.actor_id) & {"left-eye", "right-eye"}) == 2:
        raise ValidationError("Blind ranged attacks require an explicit sensory targeting adapter")
    stats = build(runtime, state, actor.actor_id).statistics
    assert stats is not None
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    validate_rated_strength(catalog(runtime).profile_id, weapon, st)
    range_st = weapon.rated_strength.st if weapon.rated_strength is not None else st
    if scene.distance > float(weapon.maximum_range) * (
        range_st if weapon.range_basis == "st" else 1
    ):
        raise ValidationError("Target exceeds maximum ranged weapon range")
    if shots > weapon.rate_of_fire or (
        shots > 1 and catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Unsupported fire mode or shot count for profile")
    from wayfarer.engine.simulation.combat.objects.locations import validate_target

    validate_target(
        runtime, state, encounter, actor.actor_id, target.actor_id, weapon, hit_location
    )
    if actor.last_maneuver == "feint" or (
        actor.last_maneuver == "all_out_attack"
        and actor.maneuver_state.attack_bonus != 4
        and pending.suppression_zone_id is None
    ):
        raise ValidationError("Ranged All-Out Attack supports Determined only")
    item = next(i for i in state.resources.items if i.id == pending.weapon_id)
    from wayfarer.engine.simulation.combat.firearm_transitions import validate_attack

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
    for candidate in () if indirect else ("dodge", "block", "parry"):
        from wayfarer.engine.simulation.combat.objects.combat import weapon_target

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
        if candidate == "block" and not (weapon.thrown or weapon.blockable):
            continue
        try:
            from wayfarer.engine.simulation.combat.tactical import defense_adjustment

            defense_adjustment(encounter, actor, target)
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
            from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense

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
                }
            )
        }
    )
