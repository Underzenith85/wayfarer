"""Ranged dispatch in the encounter transaction (Lite 27-29; Basic B372-375).

Numeric baseline: Fourth Edition (2004). Exact-printing audit remains a
certification gate. No alternative inventory, injury or command receipt engine.
"""

from __future__ import annotations

from dataclasses import replace
from math import sqrt
from typing import TYPE_CHECKING, cast

from wayfarer.engine.rules.checks import Outcome, draw_dice
from wayfarer.engine.rules.combat_tables import minimum_strength_penalty
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.location_types import HitLocation, HumanLocation
from wayfarer.engine.rules.ranged_tables import (
    multiple_projectile_attack,
    range_penalty,
    rapid_fire_bonus,
)
from wayfarer.engine.rules.spray_types import Stream
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat import (
    ActiveSuppressionZone,
    BasicSpatialContext,
    CombatEngine,
    Defense,
    Encounter,
    InjuryTrace,
    PendingDefense,
    PendingSprayTarget,
    RangedSituation,
)
from wayfarer.engine.simulation.condition_checks import check_modifiers
from wayfarer.engine.simulation.entangle import attack_penalty as entangle_attack_penalty
from wayfarer.engine.simulation.entangle import bind as entangle_bind
from wayfarer.engine.simulation.fatigue import fatigue_value
from wayfarer.engine.simulation.gurps_equipment import RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.hit_locations import (
    attack_penalty,
    missing_location,
    part,
    select_location,
)
from wayfarer.engine.simulation.injury import Wound, apply_injury
from wayfarer.engine.simulation.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.resources import AmmunitionLoad, ResourceState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat_commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def declare(
    runtime: RulesContext, encounter: Encounter, situations: tuple[RangedSituation, ...]
) -> Encounter:
    if not situations:
        return encounter
    if runtime.rules.combat and runtime.rules.combat.gurps_equipment is not None:
        from wayfarer.engine.simulation.mechanics.gurps_melee import catalog

        catalog(runtime)
    keys = [(s.attacker_id, s.defender_id) for s in situations]
    if len(set(keys)) != len(keys) or any(
        a == b or a not in encounter.turn_order or b not in encounter.turn_order for a, b in keys
    ):
        raise ValidationError("Ranged scene requires unique directed participant pairs")
    if isinstance(encounter.spatial, BasicSpatialContext):
        if any(
            item.distance_yards is not None
            or encounter.spatial.active("distance", item.attacker_id, item.defender_id) is None
            for item in situations
        ):
            raise ValidationError(
                "Basic ranged situations derive distance from authoritative spatial facts"
            )
    elif any(item.distance_yards is None for item in situations):
        raise ValidationError("Mapped ranged situations require declared distance")
    return encounter.model_copy(update={"ranged_situations": situations})


def situation(
    runtime: RulesContext,
    encounter: Encounter,
    attacker: str,
    defender: str,
    weapon: RangedMode | None = None,
    *,
    ground: bool = False,
) -> RangedSituation:
    value = next(
        (
            s
            for s in encounter.ranged_situations
            if (s.attacker_id, s.defender_id) == (attacker, defender)
        ),
        None,
    )
    if value is None:
        raise ValidationError("Ranged attack requires declared scene distance, speed and size")
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.hex_geometry import ranged_distance
        from wayfarer.engine.simulation.tactical import attack_geometry, pose

        actor = next(p for p in encounter.participants if p.actor_id == attacker)
        target = next(p for p in encounter.participants if p.actor_id == defender)
        attack_geometry(encounter, actor, target, board=runtime.hex_map(encounter))
        distance = ranged_distance(
            runtime.require_hex(encounter),
            pose(actor).position,
            pose(target).position,
            beam=bool(weapon and weapon.damage.tight_beam),
        )
        value = value.model_copy(update={"distance_yards": float(distance)})
    elif encounter.spatial_kind == "basic":
        from wayfarer.engine.simulation.combat import basic_distance

        value = value.model_copy(
            update={"distance_yards": basic_distance(encounter, attacker, defender)}
        )
    if ground:
        actor = next(p for p in encounter.participants if p.actor_id == attacker)
        target = next(p for p in encounter.participants if p.actor_id == defender)
        value = value.model_copy(
            update={
                "speed_yards_per_second": 0.0,
                "distance_yards": value.distance
                if encounter.spatial_kind in ("hex", "basic")
                else float(CombatEngine.distance(actor.position, target.position)),
            }
        )
    return value


def _spray_vector(encounter: Encounter, actor_id: str, target_id: str) -> tuple[float, float]:
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    target = next(p for p in encounter.participants if p.actor_id == target_id)
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.hex_geometry import Hex

        if not isinstance(actor.position, Hex) or not isinstance(target.position, Hex):
            raise ValidationError("Spraying fire requires one mapped coordinate system")
        return (
            (target.position.q + target.position.r / 2) - (actor.position.q + actor.position.r / 2),
            (target.position.r - actor.position.r) * sqrt(3) / 2,
        )
    from wayfarer.engine.simulation.combat import GridPoint

    if not isinstance(actor.position, GridPoint) or not isinstance(target.position, GridPoint):
        raise ValidationError("Spraying fire requires exact mapped positions")
    return float(target.position.x - actor.position.x), float(target.position.y - actor.position.y)


def prepare_spraying_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
) -> Encounter:
    """Validate and persist a B409 ordered multi-target sweep before any dice."""
    if not command.spray_targets:
        return encounter
    if encounter.spatial_kind == "basic":
        raise ValidationError("Spraying fire requires exact mapped target directions")
    if command.target_item_id is not None or command.second_target_id is not None:
        raise ValidationError("Spraying fire targets combatants through one declared sweep")
    pending = encounter.pending_defense
    if pending is None or pending.mode_id is None:
        raise ValidationError("Spraying fire requires a pending ranged attack")
    from wayfarer.engine.simulation.mechanics.gurps_melee import mode

    selected = mode(runtime, state, command.actor_id, command.item_id or "", pending.mode_id)
    if not isinstance(selected, RangedMode):
        raise ValidationError("Spraying fire requires a ranged weapon")
    if selected.rate_of_fire < 5 or selected.thrown or selected.sprayer is not None:
        raise ValidationError("Spraying fire requires an ordinary weapon with RoF 5+")
    declarations = (
        (pending.defender_id, pending.shots, pending.hit_location),
        *(
            (target.target_id, target.shots, target.hit_location)
            for target in command.spray_targets
        ),
    )
    target_ids = tuple(target_id for target_id, _, _ in declarations)
    if len(set(target_ids)) != len(target_ids) or command.actor_id in target_ids:
        raise ValidationError("Spraying fire requires distinct targets other than the attacker")
    vectors = tuple(
        _spray_vector(encounter, command.actor_id, target_id) for target_id in target_ids
    )
    for index, left in enumerate(vectors):
        for right in vectors[index + 1 :]:
            dot = left[0] * right[0] + left[1] * right[1]
            cross = left[0] * right[1] - left[1] * right[0]
            if dot <= 0 or 3 * cross * cross > dot * dot:
                raise ValidationError("Spraying-fire targets must fit within one 30-degree angle")
    turns = tuple(
        left[0] * right[1] - left[1] * right[0]
        for left, right in zip(vectors, vectors[1:], strict=False)
    )
    if any(turn > 0 for turn in turns) and any(turn < 0 for turn in turns):
        raise ValidationError("Spraying-fire targets must be ordered from one side to the other")
    participants = {p.actor_id: p for p in encounter.participants}
    traversal: list[int] = []
    from wayfarer.engine.simulation.mechanics.location_combat import validate_target

    for index, (target_id, shots, hit_location) in enumerate(declarations):
        target = participants.get(target_id)
        if target is None:
            raise ValidationError("Spraying-fire target is not in the encounter")
        scene = situation(runtime, encounter, command.actor_id, target_id, selected)
        if scene.distance > float(selected.maximum_range):
            raise ValidationError("Spraying-fire target exceeds maximum weapon range")
        validate_target(
            runtime, state, encounter, command.actor_id, target_id, selected, hit_location
        )
        if shots > selected.rate_of_fire or shots < selected.minimum_shots_per_attack:
            raise ValidationError("Each spraying-fire target requires a legal burst")
        if index:
            previous = participants[declarations[index - 1][0]]
            distance = CombatEngine.distance(previous.position, target.position)
            traversal.append(max(0, distance - 1) * (2 if selected.rate_of_fire > 16 else 1))
    total = sum(shots for _, shots, _ in declarations) + sum(traversal)
    if total > selected.rate_of_fire:
        raise ValidationError("Spraying-fire bursts and traversal exceed weapon RoF")
    load = next(
        (
            entry
            for entry in state.resources.ammunition_loads
            if entry.weapon_id == pending.weapon_id
        ),
        None,
    )
    if load is None or load.mode_id != selected.id or load.rounds < total:
        raise ValidationError("Spraying fire requires all declared ammunition before rolling")
    queued = tuple(
        PendingSprayTarget(
            target_id=target_id,
            shots=shots,
            hit_location=hit_location,
            recoil_penalty=index,
            traversal_shots=traversal[index - 1],
        )
        for index, (target_id, shots, hit_location) in enumerate(declarations[1:], start=1)
    )
    return encounter.model_copy(
        update={"pending_defense": pending.model_copy(update={"spray_targets": queued})}
    )


def prepare_suppression_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
    board: HexBattlefield | None,
) -> tuple[PlayState, Encounter]:
    """Validate, pay for, and persist B409-410 suppression zones."""
    if not command.suppression_zones:
        return state, encounter
    if encounter.spatial_kind != "hex" or board is None:
        raise ValidationError("Suppression fire requires an exact hex path and battlefield")
    from wayfarer.engine.simulation.mechanics.gurps_melee import mode

    selected = mode(runtime, state, command.actor_id, command.item_id or "", command.mode_id)
    if (
        not isinstance(selected, RangedMode)
        or selected.rate_of_fire < 5
        or selected.thrown
        or selected.sprayer is not None
    ):
        raise ValidationError("Suppression fire requires an ordinary weapon with RoF 5+")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if not isinstance(actor.position, Hex):
        raise ValidationError("Suppression fire requires the firer's exact hex")
    declarations = command.suppression_zones
    if len(declarations) > 1 and selected.rate_of_fire < 10:
        raise ValidationError("Multiple suppression zones require RoF 10+")
    if len({zone.center for zone in declarations}) != len(declarations):
        raise ValidationError("Suppression zones require distinct centers")
    for index, zone in enumerate(declarations):
        cell = board.cell(zone.center)
        if cell.blocked:
            raise ValidationError("Suppression-zone center cannot be blocked terrain")
        if CombatEngine.distance(actor.position, zone.center) > float(selected.maximum_range):
            raise ValidationError("Suppression zone exceeds maximum weapon range")
        if zone.shots < selected.minimum_shots_per_attack:
            raise ValidationError("Suppression zone requires a legal burst")
        if len(declarations) > 1 and zone.shots < 5:
            raise ValidationError("Each of multiple suppression zones requires at least five shots")
        if index and CombatEngine.distance(declarations[index - 1].center, zone.center) > 2:
            raise ValidationError("Multiple suppression zones must be adjacent")
    total = sum(zone.shots for zone in declarations)
    if total > selected.rate_of_fire:
        raise ValidationError("Suppression-zone shots exceed weapon RoF")
    load = next(
        (entry for entry in state.resources.ammunition_loads if entry.weapon_id == command.item_id),
        None,
    )
    if load is None or load.mode_id != selected.id or load.rounds < total:
        raise ValidationError("Suppression fire requires all declared ammunition")
    aim = actor.maneuver_state
    aim_bonus = (
        aim.aim_bonus if (aim.aim_item_id, aim.aim_mode_id) == (command.item_id, selected.id) else 0
    )
    zones = tuple(
        ActiveSuppressionZone(
            id=f"suppression:{command.id}:{index}",
            attacker_id=command.actor_id,
            weapon_id=command.item_id or "",
            mode_id=selected.id,
            origin=actor.position,
            center=zone.center,
            shots=zone.shots,
            remaining_hits=zone.shots,
            aim_bonus=aim_bonus,
            skill_cap=8 if selected.mount is not None else 6,
        )
        for index, zone in enumerate(declarations)
    )
    from wayfarer.engine.simulation.firearms import spend_rounds
    from wayfarer.engine.simulation.mechanics.firearms import validate_attack

    validate_attack(state.resources, command.item_id or "", selected, total)
    resources = spend_rounds(state.resources, command.item_id or "", total)
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), encounter.model_copy(
        update={"suppression_zones": encounter.suppression_zones + zones}
    )


def validate_command(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> None:
    if command.spray_targets and (
        command.maneuver not in ATTACK_MANEUVERS
        or command.item_id is None
        or command.target_id is None
        or command.mode_id is None
        or command.second_item_id is not None
        or command.second_target_id is not None
        or command.second_mode_id is not None
    ):
        raise ValidationError("Spraying fire requires one ranged attack and its ordered targets")
    if command.suppression_zones and (
        command.maneuver != "all_out_attack"
        or command.attack_option != "suppression"
        or command.item_id is None
        or command.mode_id is None
        or command.target_id is not None
        or command.shots != 1
        or command.spray_targets
        or command.hit_location is not None
        or command.target_item_id is not None
        or command.destination is not None
        or command.hex_path
        or command.hex_facing is not None
        or command.step_timing != "before"
        or command.second_item_id is not None
        or command.second_target_id is not None
        or command.second_mode_id is not None
    ):
        raise ValidationError("Suppression fire requires one immobile mapped All-Out Attack")
    if command.attack_option == "suppression" and not command.suppression_zones:
        raise ValidationError("Suppression fire requires at least one declared zone")
    if command.laser_sight:
        from wayfarer.engine.simulation.mechanics.gurps_melee import mode

        selected = mode(runtime, state, command.actor_id, command.item_id or "", command.mode_id)
        if command.maneuver not in ATTACK_MANEUVERS or not isinstance(selected, RangedMode):
            raise ValidationError("Laser sight requires a ranged attack")
        if selected.smartgun is None or selected.smartgun.laser_sight_bonus != 1:
            raise ValidationError("Selected weapon has no explicit laser sight")
        if command.target_id is None:
            raise ValidationError("Laser sight requires a target")
        scene = situation(runtime, encounter, command.actor_id, command.target_id, selected)
        if not scene.laser_visible_to_firer:
            raise ValidationError("Firer cannot see the laser sight dot")
    if command.recover_thrown_item:
        if (
            command.maneuver != "ready"
            or not command.item_id
            or command.reload_ammunition_id
            or command.unload_ammunition
            or command.firearm_service
            or command.fast_draw
            or command.let_down_bow
        ):
            raise ValidationError("Thrown recovery requires a dedicated Ready")
        from wayfarer.engine.simulation.mechanics.thrown_items import recover

        recover(runtime, state, encounter, command)
    if command.braced and command.maneuver != "aim":
        raise ValidationError("Bracing is selected as part of Aim")
    if command.step_timing == "after" and command.maneuver != "attack":
        raise ValidationError("Only Attack permits a step after the attack")
    if any(
        value is not None
        for value in (command.second_item_id, command.second_target_id, command.second_mode_id)
    ) and not (command.maneuver == "all_out_attack" and command.attack_option == "double"):
        raise ValidationError("Second attack choices require All-Out Attack (Double)")
    if command.wait_trigger is not None and command.wait_trigger.unarmed is not None:
        from wayfarer.engine.simulation.mechanics.unarmed import declare_unarmed_wait

        declare_unarmed_wait(runtime, state, encounter, command.actor_id, command.wait_trigger)
    if command.wait_trigger is not None and command.wait_trigger.stop_thrust:
        from wayfarer.engine.simulation.gurps_equipment import MeleeMode
        from wayfarer.engine.simulation.mechanics.gurps_melee import mode

        trigger = command.wait_trigger
        assert trigger.item_id is not None
        selected = mode(runtime, state, command.actor_id, trigger.item_id, trigger.mode_id)
        if not isinstance(selected, MeleeMode) or selected.damage.basis != "thrust":
            raise ValidationError("Stop thrust requires a ready thrusting melee mode")
    if (
        encounter.spatial_kind == "square"
        and encounter.ranged_situations
        and (command.destination is not None or command.maneuver == "move")
    ):
        raise ValidationError("Declared ranged scene movement requires the tactical adapter")
    if command.shots != 1 and (
        runtime.rules.combat is None or runtime.rules.combat.gurps_equipment is None
    ):
        raise ValidationError("Shot count requires GURPS ranged dispatch")
    if (
        command.maneuver == "ready"
        and command.mode_id is not None
        and command.reload_ammunition_id is None
        and not command.unload_ammunition
        and not command.let_down_bow
        and command.firearm_service is None
    ):
        raise ValidationError("Ready mode selection requires a reload")
    if command.shots != 1 and command.maneuver not in ATTACK_MANEUVERS:
        raise ValidationError("Shot count requires an attack")
    if command.firearm_service is not None:
        from wayfarer.engine.simulation.mechanics.firearms import service

        service(runtime, state, encounter, command, validate_only=True)
    elif command.firearm_service_skill != "weapon":
        raise ValidationError("Firearm service skill requires a service operation")
    if command.escape_entanglement:
        # Struggling free costs the whole Ready; it is not a free action bolted
        # onto a reload, an unload or an attack.
        if (
            command.maneuver != "ready"
            or command.reload_ammunition_id is not None
            or command.unload_ammunition
            or command.item_id is not None
        ):
            raise ValidationError("Escaping a binding requires its own Ready maneuver")
        actor = next((p for p in encounter.participants if p.actor_id == command.actor_id), None)
        if actor is None or actor.entangled is None:
            raise ValidationError("Nothing is binding this actor")
    if command.unload_ammunition:
        if command.maneuver != "ready" or command.reload_ammunition_id is not None:
            raise ValidationError("Unload requires a separate Ready maneuver")
        unload_weapon(runtime, state, command)
    if command.fast_draw and command.reload_ammunition_id is None:
        raise ValidationError("Fast-Draw requires an explicit projectile reload")
    if command.cocking_aid_id is not None and command.reload_ammunition_id is None:
        raise ValidationError("Cocking aids require a reload")
    if command.let_down_bow:
        if (
            command.maneuver != "ready"
            or command.reload_ammunition_id is not None
            or command.unload_ammunition
            or command.firearm_service is not None
        ):
            raise ValidationError("Bow let-down requires its own Ready")
        from wayfarer.engine.simulation.mechanics.gurps_melee import mode
        from wayfarer.engine.simulation.mechanics.projectile_readiness import let_down

        selected = mode(runtime, state, command.actor_id, command.item_id or "", command.mode_id)
        if not isinstance(selected, RangedMode):
            raise ValidationError("Let-down requires a bow mode")
        let_down(state, command, selected)
    if command.reload_ammunition_id is not None:
        if command.maneuver != "ready":
            raise ValidationError("Reload requires a Ready maneuver")
        reload_weapon(runtime, state, command, validate_only=True)


def unload_weapon(
    runtime: RulesContext, state: PlayState, command: TakeCombatTurn
) -> ResourceState:
    """Release a removable magazine's reservation; no rounds are minted or spent."""
    from wayfarer.engine.simulation.mechanics.gurps_melee import catalog

    equipment = catalog(runtime)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Unload requires the exact Basic Set profile")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Unload requires an owned weapon")
    if item.firearm_failure is not None:
        raise ValidationError("Service the firearm failure before unloading")
    loaded = next(
        (load for load in state.resources.ammunition_loads if load.weapon_id == item.id), None
    )
    if loaded is None or command.mode_id not in (None, loaded.mode_id):
        raise ValidationError("Unload requires the loaded weapon mode")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    weapon = next((m for m in entry.modes if m.id == loaded.mode_id), None)
    if isinstance(weapon, RangedMode) and weapon.readiness is not None:
        from wayfarer.engine.simulation.mechanics.projectile_readiness import unload

        return unload(state, command, weapon)
    if not isinstance(weapon, RangedMode) or weapon.reload_protocol != "magazine":
        raise ValidationError("Individual-round unloading requires its own timing protocol")
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                load for load in state.resources.ammunition_loads if load.weapon_id != item.id
            )
        }
    )
    runtime.resources.validate(result)
    return result


def reload_weapon(
    runtime: RulesContext, state: PlayState, command: TakeCombatTurn, *, validate_only: bool = False
) -> ResourceState:
    from wayfarer.engine.simulation.mechanics.gurps_melee import catalog

    equipment = catalog(runtime)
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    ammo = next((i for i in state.resources.items if i.id == command.reload_ammunition_id), None)
    if (
        item is None
        or ammo is None
        or item.id == ammo.id
        or (item.owner_id != command.actor_id or ammo.owner_id != command.actor_id)
    ):
        raise ValidationError("Reload requires owned weapon and ammunition")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    modes = [
        m
        for m in entry.modes
        if isinstance(m, RangedMode) and (command.mode_id is None or m.id == command.mode_id)
    ]
    if len(modes) != 1 or modes[0].thrown:
        raise ValidationError("Reload requires one projectile mode")
    weapon = modes[0]
    if item.firearm_failure is not None:
        raise ValidationError("Service the firearm failure before reloading")
    if weapon.readiness is not None:
        from wayfarer.engine.simulation.mechanics.projectile_readiness import reload

        return reload(runtime, state, command, weapon, validate_only=validate_only)
    if command.fast_draw or command.cocking_aid_id is not None:
        raise ValidationError("This weapon has no explicit readiness protocol")
    reload_seconds = weapon.reload_seconds
    if weapon.rated_strength is not None:
        from wayfarer.engine.simulation.mechanics.gurps_melee import build

        stats = build(runtime, state, command.actor_id).statistics
        assert stats is not None
        fp = next(p for p in state.resources.pools if p.id == f"fp:{command.actor_id}")
        st = fatigue_value(fp, stats.st)
        validate_rated_strength(equipment.profile_id, weapon, st)
        if weapon.rated_strength.kind == "crossbow":
            difference = weapon.rated_strength.st - st
            if difference >= 5:
                raise ValidationError("Crossbow ST is too high to reload")
            if difference >= 3:
                raise ValidationError("Crossbow reload requires an explicit cocking-aid protocol")
            reload_seconds = 8 if difference > 0 else 4
    if weapon.reload_protocol == "per-round" and equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Per-round reload requires the exact Basic Set profile")
    if ammo.definition_id != weapon.ammunition_id or item.quantity != 1:
        raise ValidationError("Reload ammunition does not match this individual weapon")
    old = next(
        (loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id == item.id), None
    )
    if old and (old.mode_id != weapon.id or old.ammunition_item_id != ammo.id):
        raise ValidationError("Unload the existing ammunition before changing reload source")
    rounds = old.rounds if old else 0
    if rounds >= weapon.shots:
        raise ValidationError("Weapon is already fully loaded")
    reserved = sum(
        loaded.rounds
        for loaded in state.resources.ammunition_loads
        if loaded.ammunition_item_id == ammo.id
    )
    available_units = ammo.quantity if ammo.charges is None else ammo.charges
    if available_units <= reserved:
        raise ValidationError("No unreserved ammunition remains")
    progress = (old.reload_progress if old else 0) + 1
    if progress >= max(1, reload_seconds):
        rounds += min(
            1 if weapon.reload_protocol == "per-round" else weapon.shots - rounds,
            available_units - reserved,
        )
        progress = 0
    load = AmmunitionLoad(
        weapon_id=item.id,
        mode_id=weapon.id,
        ammunition_item_id=ammo.id,
        rounds=rounds,
        reload_progress=progress,
    )
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id != item.id
            )
            + (load,)
        }
    )
    runtime.resources.validate(result)
    return result


def validate_rated_strength(profile_id: str, weapon: RangedMode, st: int) -> None:
    if weapon.rated_strength is None:
        return
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Rated weapon ST requires the exact Basic Set profile")
    if weapon.rated_strength.kind == "bow" and weapon.rated_strength.st > st:
        raise ValidationError("Bow ST exceeds the wielder's effective ST")


def _schedule_lingering_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    pending: PendingDefense,
    target_id: str,
    basic_damage: int,
    hit: bool,
) -> PlayState:
    """Bind a B433 clothing fire to this scene and the shared hazard clock."""
    if not hit or weapon.sprayer is None or not weapon.sprayer.ignites or basic_damage < 3:
        return state
    assert encounter.scene_id is not None
    import hashlib
    import json

    from wayfarer.engine.rules.hazard_types import HazardSchedule, HazardSpec
    from wayfarer.engine.simulation.hazards import HazardCommand, apply_hazard
    from wayfarer.engine.simulation.mechanics.gurps_melee import build
    from wayfarer.engine.simulation.mechanics.spell_effects import armor

    source = (
        "sprayer-fire:"
        + hashlib.sha256(
            json.dumps(
                [encounter.id, pending.attacker_id, pending.weapon_id, weapon.id, target_id],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    if any(
        hazard.active and hazard.spec.id == source and hazard.actor_id == target_id
        for hazard in state.resources.hazards
    ):
        return state
    serial = sum(
        hazard.spec.id == source and hazard.actor_id == target_id
        for hazard in state.resources.hazards
    )
    schedule_id = (
        "exposure:"
        + hashlib.sha256(
            json.dumps([source, target_id, serial], separators=(",", ":")).encode()
        ).hexdigest()
    )
    compiled = build(runtime, state, target_id)
    assert compiled.statistics is not None
    spec = HazardSpec(
        id=source,
        kind="fire",
        scene_id=encounter.scene_id,
        delay=1,
        interval=1,
        # Fire remains until an authoritative leave/extinguish action ends it.
        # The large bound prevents an unbounded persisted schedule.
        cycles=100000,
        damage_dice=1,
        damage_add=-1 if basic_damage >= 10 else -4,
        resistible=False,
        reference="Basic Set B433/B400",
    )
    schedule = HazardSchedule(
        id=schedule_id,
        actor_id=target_id,
        spec=spec,
        started=state.resources.game_time,
        due=state.resources.game_time + 1,
        remaining=spec.cycles,
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        swimming=compiled.statistics.ht,
        resistance=armor(runtime, state, target_id, large_area=True),
        full_hp=compiled.statistics.hp,
    )
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id=schedule_id + ":enter",
            actor_id=target_id,
            expected_revision=state.resources.revision,
            kind="enter",
            hazard_id=source,
        ),
        schedule,
        rng=runtime.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources})


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
    from wayfarer.engine.simulation.mechanics.gurps_melee import build, catalog, defense_value

    pending = encounter.pending_defense
    assert pending is not None
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    geometry = encounter
    if target_item_id:
        from wayfarer.engine.simulation.mechanics.object_combat import target_geometry

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
    from wayfarer.engine.simulation.mechanics.location_combat import disabled

    if len(disabled(state, actor.actor_id) & {"left-eye", "right-eye"}) == 2:
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
    from wayfarer.engine.simulation.mechanics.location_combat import validate_target

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
    from wayfarer.engine.simulation.mechanics.firearms import validate_attack

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
        from wayfarer.engine.simulation.mechanics.object_combat import weapon_target

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
            from wayfarer.engine.simulation.tactical import defense_adjustment

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
            from wayfarer.engine.simulation.mechanics.unarmed import unarmed_defense

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


def expend(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    shots: int | None = None,
    hit: bool = False,
    catcher_id: str | None = None,
    hand: str | None = None,
) -> tuple[PlayState, Encounter]:
    from wayfarer.engine.simulation.mechanics.gurps_melee import catalog

    pending = encounter.pending_defense
    assert pending is not None
    resources = state.resources
    if weapon.thrown:
        item = next(i for i in resources.items if i.id == pending.weapon_id)
        if catalog(runtime).profile_id == "gurps-basic-set-4e-2004":
            from wayfarer.engine.simulation.mechanics.thrown_items import landed

            resources, encounter = landed(
                runtime, state, encounter, item, hit=hit, catcher_id=catcher_id, hand=hand
            )
        else:
            resources = resources.model_copy(
                update={
                    "items": tuple(i for i in resources.items if i.id != item.id),
                    "expended_items": resources.expended_items
                    + (
                        item.model_copy(
                            update={"equipped": False, "ready": False, "container_id": None}
                        ),
                    ),
                }
            )
        actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "ready_item_ids": tuple(
                        i for i in actor.ready_item_ids if i != pending.weapon_id
                    ),
                    "hand_bindings": tuple(
                        (i, h) for i, h in actor.hand_bindings if i != pending.weapon_id
                    ),
                }
            ),
        )
    else:
        from wayfarer.engine.simulation.firearms import spend_rounds

        resources = spend_rounds(
            resources, pending.weapon_id, pending.shots if shots is None else shots
        )
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), encounter


def resolve(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    selected: Defense,
    item_id: str | None,
    *,
    second_defense: Defense | None,
    second_item_id: str | None,
    parry_mode_id: str | None = None,
    second_parry_mode_id: str | None = None,
    catch_thrown: bool = False,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    from wayfarer.engine.simulation.mechanics.gurps_maneuvers import distracted
    from wayfarer.engine.simulation.mechanics.gurps_melee import (
        build,
        catalog,
        defense_value,
        level,
    )
    from wayfarer.engine.simulation.mechanics.weapon_flight import position

    original_resources = state.resources
    pending = encounter.pending_defense
    assert pending is not None
    if selected not in pending.allowed or (
        second_defense and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense cannot stop this projectile")
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    original_target = target
    geometry = encounter
    if pending.target_item_id:
        from wayfarer.engine.simulation.mechanics.object_combat import target_geometry

        geometry = target_geometry(runtime, state, encounter, pending.target_item_id)
    scene = situation(
        runtime,
        geometry,
        actor.actor_id,
        target.actor_id,
        weapon,
        ground=bool(
            pending.target_item_id
            and next(i for i in state.resources.items if i.id == pending.target_item_id).ground
        ),
    )
    equipment = catalog(runtime)
    compiled = build(runtime, state, actor.actor_id)
    defender_build = build(runtime, state, target.actor_id)
    stats = compiled.statistics
    defender_stats = defender_build.statistics
    assert stats is not None and defender_stats is not None
    value = level(compiled, weapon.skill_id)
    actor_hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    validate_rated_strength(equipment.profile_id, weapon, st)
    aim = actor.maneuver_state
    aimed = (
        (aim.aim_item_id, aim.aim_mode_id, aim.aim_target_id)
        == (
            pending.weapon_id,
            weapon.id,
            target.actor_id,
        )
        and aim.aim_seconds > 0
        and not pending.vehicle_aim_lost
    )
    bonus = (
        pending.suppression_aim_bonus
        if pending.suppression_zone_id is not None
        else aim.aim_bonus
        if aimed
        else 0
    )
    if pending.suppression_zone_id is None:
        if actor.last_maneuver == "move_and_attack":
            bonus = min(-2, weapon.bulk)
        elif actor.last_maneuver == "all_out_attack":
            bonus += 1
    effective_shots = pending.shots
    close_projectile_multiplier = 1
    if weapon.multiple_projectiles is not None:
        assert weapon.half_damage_range is not None
        effective_shots, close_projectile_multiplier = multiple_projectile_attack(
            pending.shots,
            weapon.multiple_projectiles.projectiles_per_shot,
            scene.distance,
            float(weapon.half_damage_range),
        )
    attack_target = (
        int(value.value)
        + bonus
        + scene.size_modifier
        + range_penalty(scene.distance + scene.speed_yards_per_second)
        + rapid_fire_bonus(effective_shots)
        # A mount bears the weapon, so the firer's own ST is not what limits it.
        - (0 if weapon.mount is not None else minimum_strength_penalty(weapon.minimum_st, st))
        + pending.vehicle_attack_penalty
    )
    if pending.laser_sight and scene.laser_visible_to_firer:
        attack_target += 1
    if pending.target_item_id:
        from wayfarer.engine.simulation.mechanics.object_combat import target_modifier

        attack_target += (
            target_modifier(runtime, state, target.actor_id, pending.target_item_id)
            - scene.size_modifier
        )
    attack_target += entangle_attack_penalty(actor)
    attack_target -= actor_hp.injury.shock if actor_hp.injury else 0
    if actor_hp.injury and pending.suppression_zone_id is None:
        attack_target += actor_hp.injury.physical_traits.darkness(encounter.darkness_penalty)
    from wayfarer.engine.simulation.mechanics.location_combat import disabled

    eyes = disabled(state, actor.actor_id) & {"left-eye", "right-eye"}
    if eyes:
        attack_target -= (
            6 if len(eyes) == 2 else 1 if aimed and actor.last_maneuver != "move_and_attack" else 3
        )
    if pending.hit_location:
        entries = {e.definition_id: e for e in equipment.entries}
        shield_side = next(
            (
                hand.split("-")[0]
                for item, hand in target.hand_bindings
                if any(
                    i.id == item and entries[i.definition_id].shield for i in state.resources.items
                )
            ),
            None,
        )
        attack_target += attack_penalty(pending.hit_location, shield_side=shield_side)
    if pending.suppression_skill_cap is not None:
        attack_target = min(
            attack_target,
            pending.suppression_skill_cap + rapid_fire_bonus(effective_shots),
        )
    defense_value_, defense_item = defense_value(
        runtime, state, target, selected, item_id, parry_mode_id=parry_mode_id
    )
    second_value, second_item = defense_value(
        runtime,
        state,
        target,
        second_defense or "none",
        second_item_id,
        parry_mode_id=second_parry_mode_id,
    )
    if pending.laser_sight and scene.laser_visible_to_target:
        if selected == "dodge" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value + 1, ())
        if second_defense == "dodge" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value + 1, ())
    if weapon.thrown:
        thrown_item = next(i for i in state.resources.items if i.id == pending.weapon_id)
        entry = next(e for e in equipment.entries if e.definition_id == thrown_item.definition_id)
        penalty = 2 if entry.weight_millipounds <= 1000 else 1
        if selected == "parry" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value - penalty, ())
        if second_defense == "parry" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value - penalty, ())
    from wayfarer.engine.simulation.mechanics.firearms import (
        before_attack,
        roll_malfunction,
        set_failure,
    )

    state = state.model_copy(
        update={"resources": before_attack(state.resources, pending.weapon_id, weapon)}
    )
    attack = success_roll(
        equipment.profile_id,
        attack_target,
        check_modifiers(state.resources, actor.actor_id, "dx"),
        rng=runtime.rng,
    )
    original_attack = attack
    attack, shots_fired, malfunction_table, failure = roll_malfunction(
        runtime,
        weapon,
        attack,
        cause_id=pending.id,
        shots=pending.shots,
        rapid_bonus=rapid_fire_bonus(effective_shots),
    )
    if failure is not None:
        state = state.model_copy(
            update={"resources": set_failure(state.resources, pending.weapon_id, failure)}
        )
    # B382 excludes ranged attacks from the generic failure-by-ten rule.
    if equipment.profile_id == "gurps-basic-set-4e-2004":
        attack = replace(attack, rule_id="gurps.combat.ranged_attack")
        if attack.outcome is Outcome.CRITICAL_FAILURE and attack.total < 17:
            attack = replace(attack, outcome=Outcome.FAILURE)
    from wayfarer.engine.simulation.hit_locations import location_special_effects, torso_near_miss

    near_miss = bool(shots_fired) and torso_near_miss(pending.hit_location, attack)
    effective_shots_fired = (
        shots_fired
        if weapon.multiple_projectiles is None or close_projectile_multiplier > 1
        else shots_fired * weapon.multiple_projectiles.projectiles_per_shot
    )
    hits = (
        min(
            effective_shots_fired,
            1
            + max(0, attack.effective_target - sum(attack.dice))
            // (weapon.recoil + pending.spray_recoil_penalty),
        )
        if attack.outcome.succeeded
        else int(near_miss)
    )
    if pending.suppression_zone_id is not None:
        hits = min(hits, pending.suppression_remaining_hits)
    initial_hits = hits
    defense = None
    second_trace = None
    if hits and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_value_ is not None:
        defense = success_roll(equipment.profile_id, int(defense_value_.value), rng=runtime.rng)
        if defense.outcome.succeeded:
            avoided = (
                hits
                if defense.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(defense_value_.value) - sum(defense.dice) if selected == "dodge" else 1
                )
            )
            hits = max(0, hits - avoided)
        if selected == "parry" and defense_item is not None:
            target = target.model_copy(update={"parries": target.parries + (defense_item,)})
        if selected == "block":
            target = target.model_copy(update={"block_used": True})
        if defense.outcome is Outcome.CRITICAL_FAILURE and selected == "dodge":
            target = target.model_copy(update={"posture": "prone"})
    if hits and defense is not None and not defense.outcome.succeeded and second_value is not None:
        from wayfarer.engine.simulation.mechanics.object_combat import defense_stress

        state, encounter = defense_stress(
            runtime,
            state,
            encounter,
            target.actor_id,
            pending.id + ":second",
            second_item,
        )
        target = target.model_copy(
            update={
                "ready_item_ids": tuple(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == target.actor_id and i.equipped and i.ready
                )
            }
        )
        try:
            second_value, second_item = defense_value(
                runtime,
                state,
                target,
                second_defense or "none",
                second_item,
                parry_mode_id=second_parry_mode_id,
            )
            if weapon.thrown and second_defense == "parry" and second_value:
                second_value = DerivedValue(second_value.target, second_value.value - penalty, ())
        except ValidationError:
            second_value = None
    if hits and defense is not None and not defense.outcome.succeeded and second_value is not None:
        second_trace = success_roll(equipment.profile_id, int(second_value.value), rng=runtime.rng)
        if second_trace.outcome.succeeded:
            avoided = (
                hits
                if second_trace.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(second_value.value) - sum(second_trace.dice)
                    if second_defense == "dodge"
                    else 1
                )
            )
            hits = max(0, hits - avoided)
        if second_defense == "parry" and second_item is not None:
            target = target.model_copy(update={"parries": target.parries + (second_item,)})
        if second_defense == "block":
            target = target.model_copy(update={"block_used": True})
        if second_trace.outcome is Outcome.CRITICAL_FAILURE and second_defense == "dodge":
            target = target.model_copy(update={"posture": "prone"})
    from wayfarer.engine.simulation.mechanics.object_combat import intercepted_projectiles

    shield_hit, shield_impacts = intercepted_projectiles(
        runtime,
        state,
        encounter,
        second_trace or defense,
        second_defense if second_trace else selected,
        initial_hits,
    )
    impacts = hits + shield_impacts
    dropped = {
        equipment_id
        for choice, roll, equipment_id in (
            (selected, defense, defense_item),
            (second_defense, second_trace, second_item),
        )
        if choice == "block"
        and roll is not None
        and roll.outcome is Outcome.CRITICAL_FAILURE
        and equipment_id is not None
    }
    if dropped:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False}) if i.id in dropped else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    critical_table = (
        draw_dice(runtime.rng, 3)
        if equipment.profile_id == "gurps-basic-set-4e-2004"
        and attack.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS)
        else ()
    )
    critical = sum(critical_table) if attack.outcome is Outcome.CRITICAL_SUCCESS else 0
    blocked = "ranged-critical-table" if critical_table and not critical else None
    critical_parry = next(
        (
            (equipment_id, selected_mode)
            for choice, roll, equipment_id, selected_mode in (
                (selected, defense, defense_item, parry_mode_id),
                (second_defense, second_trace, second_item, second_parry_mode_id),
            )
            if choice == "parry" and roll is not None and roll.outcome is Outcome.CRITICAL_FAILURE
        ),
        None,
    )
    parry_item, critical_parry_mode = critical_parry or (None, None)
    if parry_item is not None:
        critical_table = draw_dice(runtime.rng, 3)
        blocked = "ranged-critical-parry"
    critical_rolls: tuple[tuple[int, int, int], ...] = (
        (cast(tuple[int, int, int], critical_table),) if critical_table else ()
    )
    miss_effect_dice: tuple[int, ...] = ()
    miss_lasting_ids: tuple[str, ...] = ()
    if blocked and parry_item in ("left-hand", "right-hand"):
        from wayfarer.engine.simulation.mechanics.unarmed import critical_miss
        from wayfarer.engine.simulation.unarmed import PendingUnarmed

        encounter = CombatEngine._replace(encounter, target)
        state, encounter, checks, dice, handled = critical_miss(
            runtime,
            state,
            encounter,
            PendingUnarmed(
                id=pending.id,
                actor_id=pending.attacker_id,
                target_id=pending.defender_id,
                action="punch",
                skill="attribute:dx",
                hands=(),
                allowed=("none", "parry"),
            ),
            target.actor_id,
            critical_table,
            parry_item,
        )
        miss_effect_dice = dice + tuple(d for check in checks for d in check.dice)
        blocked = None if handled else "ranged-critical-unarmed-parry"
        target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    elif blocked:
        from wayfarer.engine.simulation.mechanics.ranged_misses import resolve_miss

        # Preserve defense counters/posture before applying consequences to the defender.
        encounter = CombatEngine._replace(encounter, target)
        state, encounter, miss, blocked = resolve_miss(
            runtime,
            state,
            encounter,
            critical_table,
            parry_item=parry_item,
            parry_mode_id=critical_parry_mode,
        )
        critical_rolls = miss.table_rolls
        critical_table = miss.table_rolls[-1]
        miss_effect_dice = miss.location_dice + miss.damage_dice
        miss_lasting_ids = miss.lasting_injury_ids
        if parry_item is not None:
            target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    caught_hand = next(
        (
            equipment_id
            for choice, roll, equipment_id in (
                (selected, defense, defense_item),
                (second_defense, second_trace, second_item),
            )
            if catch_thrown
            and choice == "parry"
            and equipment_id in ("left-hand", "right-hand")
            and roll is not None
            and roll.outcome is Outcome.CRITICAL_SUCCESS
        ),
        None,
    )
    if failure is not None and pending.spray_targets:
        encounter = encounter.model_copy(
            update={"pending_defense": pending.model_copy(update={"spray_targets": ()})}
        )
    encounter = CombatEngine._replace(encounter, target)
    if pending.suppression_zone_id is None:
        state, encounter = expend(
            runtime,
            state,
            encounter,
            weapon,
            shots=(shots_fired + pending.traversal_shots)
            if weapon.sprayer is None
            else weapon.sprayer.rounds_per_second,
            hit=bool(hits),
            catcher_id=target.actor_id if caught_hand else None,
            hand=caught_hand,
        )
    target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    from wayfarer.engine.simulation.mechanics.weapon_explosions import schedule_payload

    state, encounter, payload_attack = schedule_payload(
        runtime,
        state,
        encounter,
        weapon,
        original_resources=original_resources,
        failure=failure,
        hits=hits,
        shots_fired=shots_fired,
        critical=critical,
    )
    target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    if payload_attack:
        hits = 0
        impacts = shield_impacts
    if pending.suppression_zone_id is not None:
        encounter = encounter.model_copy(
            update={
                "suppression_zones": tuple(
                    zone.model_copy(
                        update={"remaining_hits": max(0, zone.remaining_hits - impacts)}
                    )
                    if zone.id == pending.suppression_zone_id
                    else zone
                    for zone in encounter.suppression_zones
                    if not (
                        failure is not None
                        and zone.attacker_id == pending.attacker_id
                        and zone.weapon_id == pending.weapon_id
                    )
                )
            }
        )
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    if hits and pending.hit_location:
        from wayfarer.engine.simulation.mechanics.location_combat import from_behind

        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=runtime.rng,
            from_behind=from_behind(actor, target),
        )
        if pending.hit_location == "random" and hp.injury and missing_location(hp.injury, location):
            location = "torso"
    # B373/B556: a burst rolls the critical table once. The critical projectile may
    # be redirected (eye); the remaining projectiles keep the declared location.
    base_location, base_location_dice = location, location_dice
    head = (
        location in ("skull", "face", "left-eye", "right-eye")
        and weapon.damage.damage_type != "tox"
        and hp.injury is not None
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    lasting_ids: tuple[str, ...] = miss_lasting_ids
    effect_dice: tuple[int, ...] = miss_effect_dice
    if critical and head and blocked is None:
        from wayfarer.engine.simulation.mechanics.location_combat import from_behind

        if critical in (6, 7) and location in ("face", "skull"):
            if from_behind(actor, target) or (
                hp.injury and hp.injury.tolerance and hp.injury.tolerance.no_eyes
            ):
                critical = 4
            else:
                eye_die = draw_dice(runtime.rng, 1)[0]
                effect_dice += (eye_die,)
                location = "right-eye" if eye_die <= 3 else "left-eye"
                critical_eye = True
        if critical == 8:
            target = target.model_copy(update={"forced_do_nothing": True})
    damages: list[int] = []
    injuries: list[int] = []
    damage_dice: list[int] = []
    entries = {e.definition_id: e for e in equipment.entries}

    def armor_dr() -> int:
        return max(
            (
                armor.dr
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.equipped
                and (i.condition is None or not i.condition.disabled)
                for armor in (entries[i.definition_id].armor,)
                if armor is not None
                and (
                    (location or "torso") in armor.locations
                    or (part(location) + "s" if location else "torso") in armor.locations
                )
            ),
            default=0,
        )

    dr_bonus = 0
    from wayfarer.engine.simulation.abilities import damage_resistance

    if runtime.rules.abilities is not None:
        dr_bonus = damage_resistance(
            state.resources, target.actor_id, build_revision=defender_build.revision
        )
    environmental_dr = scene.beam_environment_dr if weapon.beam_environment_dr else 0
    from wayfarer.engine.simulation.hex_geometry import Hex

    vehicle_cover = max(
        (
            transport.occupant_cover_dr
            for transport in state.resources.transports
            if target.actor_id in transport.occupants
            and encounter.spatial_kind == "hex"
            and isinstance(target.position, Hex)
            and target.position == Hex(q=transport.q, r=transport.r)
            and target.hex_facing == transport.facing
        ),
        default=0,
    )
    dr = (armor_dr() + dr_bonus + environmental_dr + vehicle_cover) * close_projectile_multiplier
    first_location, first_location_dice, first_dr = location, location_dice, dr
    hit_resistances: list[int] = []
    hit_locations: list[HumanLocation | None] = []
    hit_location_dice: list[tuple[int, ...]] = []
    expression = stats.swing if weapon.damage.basis == "swing" else stats.thrust
    range_st = st
    if weapon.rated_strength is not None:
        from wayfarer.engine.character.statistics import damage as strength_damage

        range_st = weapon.rated_strength.st
        expression = strength_damage(equipment.profile_id, range_st)[0]
    count = (weapon.damage.dice or expression.dice) * close_projectile_multiplier
    adds = (
        weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    ) * close_projectile_multiplier
    half = weapon.half_damage_range is not None and scene.distance >= float(
        weapon.half_damage_range
    ) * (range_st if weapon.range_basis == "st" else 1)
    resistance_damage = (
        weapon.damage
        if close_projectile_multiplier == 1
        else weapon.damage.model_copy(
            update={
                "armor_divisor": weapon.damage.armor_divisor / close_projectile_multiplier,
            }
        )
    )
    resistance_weapon = (
        weapon
        if close_projectile_multiplier == 1
        else weapon.model_copy(update={"damage": resistance_damage})
    )
    for index in range(impacts if blocked is None else 0):
        if index and pending.hit_location == "random":
            from wayfarer.engine.simulation.mechanics.location_combat import from_behind

            location, location_dice = select_location(
                "random",
                rng=runtime.rng,
                from_behind=from_behind(actor, target),
            )
            current_hp = next(p for p in state.resources.pools if p.id == hp.id)
            if current_hp.injury and missing_location(current_hp.injury, location):
                location = "torso"
            dr = (armor_dr() + dr_bonus + vehicle_cover) * close_projectile_multiplier
        elif index and location != base_location:
            location, location_dice = base_location, base_location_dice
            dr = (armor_dr() + dr_bonus + vehicle_cover) * close_projectile_multiplier
        hit_resistances.append(dr)
        hit_locations.append(location)
        hit_location_dice.append(location_dice)
        hit_critical = critical if index == 0 else 0
        maximum = hit_critical in ((3, 15) if head else (6, 15)) or (
            equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
        )
        dice = () if maximum else draw_dice(runtime.rng, count)
        damage_dice.extend(dice)
        damage = max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * count if maximum else sum(dice)) + adds,
        )
        damage *= (
            3
            if hit_critical in ((18,) if head else (3, 18))
            else 2
            if hit_critical in ((16,) if head else (5, 16))
            else 1
        )
        if half:
            damage //= 2
        acceleration = weapon.rocket_acceleration
        if acceleration is not None:
            damage //= (
                acceleration.close_damage_divisor
                if scene.distance <= acceleration.close_max_yards
                else acceleration.medium_damage_divisor
                if scene.distance <= acceleration.medium_max_yards
                else 1
            )
        from wayfarer.engine.simulation.mechanics.object_combat import damage_target, shield_damage

        if pending.target_item_id:
            state, encounter, object_result = damage_target(
                runtime,
                state,
                encounter,
                pending.target_item_id,
                damage,
                resistance_damage,
                impact=index,
            )
            damages.append(damage)
            injuries.append(0)
            hit_resistances[-1] = object_result.effective_dr if object_result else 0
            if object_result:
                effect_dice += tuple(d for roll in object_result.checks for d in roll)
            continue
        if shield_hit and index < shield_impacts:
            state, encounter, damage = shield_damage(
                runtime,
                state,
                encounter,
                shield_hit,
                damage,
                resistance_weapon,
                impact=index,
            )
            if damage == 0:
                damages.append(0)
                injuries.append(0)
                continue
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=f"{pending.id}:hit:{index}",
                actor_id=target.actor_id,
                expected_revision=state.resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type=weapon.damage.damage_type,
                location=location,
                critical_eye=critical_eye and index == 0,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
            ),
            ht=defender_stats.ht,
            rng=runtime.rng,
            system=True,
            dx=defender_stats.dx,
            force_major_wound=hit_critical in ((4, 5) if head else (7, 13, 14)),
            double_shock=hit_critical == 8 and not head,
            funny_bone=hit_critical == 8 and not head,
            halve_dr=("up" if head else "down")
            if hit_critical in ((4, 5, 17) if head else (4, 17))
            else None,
            ignore_dr=head and hit_critical == 3,
            held_item_locations=target.hand_bindings,
            shield_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.ready
                and i.equipped
                and entries[i.definition_id].shield
            ),
            held_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id and i.ready and i.equipped
            ),
            head_trauma=(
                "deafened"
                if head and hit_critical in (12, 13) and weapon.damage.damage_type == "cr"
                else "scarred"
                if head and hit_critical in (12, 13)
                else None
            ),
            scar_levels=2 if weapon.damage.damage_type in ("burn", "cor") else 1,
        )
        state = state.model_copy(update={"resources": resources})
        damages.append(damage)
        injuries.append(result.injury)
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
    if critical == 12 and not head and blocked is None and not pending.target_item_id:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "ready": False,
                                    "equipped": False,
                                    "container_id": None,
                                    "ground": position(encounter, target),
                                }
                            )
                            if i.owner_id == target.actor_id and i.ready
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    if head and critical == 14 and blocked is None and not pending.target_item_id:
        held_weapons = tuple(
            i.id
            for i in state.resources.items
            if i.owner_id == target.actor_id
            and i.ready
            and i.equipped
            and entries[i.definition_id].modes
        )
        if held_weapons:
            die = draw_dice(runtime.rng, 1)[0] if len(held_weapons) > 1 else None
            if die is not None:
                effect_dice += (die,)
            drop = held_weapons[0 if die is None or die <= 3 else 1]
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={
                                        "ready": False,
                                        "equipped": False,
                                        "container_id": None,
                                        "ground": position(encounter, target),
                                    }
                                )
                                if i.id == drop
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
    if weapon.sprayer is not None:
        state = _schedule_lingering_fire(
            runtime,
            state,
            encounter,
            weapon,
            pending=pending,
            target_id=target.actor_id,
            basic_damage=max(damages, default=0),
            hit=bool(hits and pending.target_item_id is None),
        )
        # Hold the stream for this second, walking it to whichever target the
        # firer laid it on. Every second is paid for and rolled separately.
        held = actor.stream
        opened = (
            held.sustain(target.actor_id)
            if held is not None and (held.weapon_id, held.mode_id) == (pending.weapon_id, weapon.id)
            else Stream(
                weapon_id=pending.weapon_id,
                mode_id=weapon.id,
                target_id=target.actor_id,
                seconds=1,
                sustained_seconds=weapon.sprayer.sustained_seconds,
                ignites=weapon.sprayer.ignites,
            )
        )
        encounter = CombatEngine._replace(encounter, actor.model_copy(update={"stream": opened}))
    updated = next(p for p in state.resources.pools if p.id == hp.id)
    assert updated.injury is not None
    # B181/B211: a landed binding holds the target whatever damage it also did.
    # A defended-away or blocked shot binds nothing.
    if weapon.entangle is not None and hits and blocked is None:
        target = entangle_bind(
            target,
            weapon.entangle,
            source_actor_id=actor.actor_id,
            weapon_definition_id=next(
                i.definition_id
                for i in (*state.resources.items, *state.resources.expended_items)
                if i.id == pending.weapon_id
            ),
            mode_id=weapon.id,
        )
    target = target.model_copy(
        update={
            "posture": "prone" if updated.injury.prone else target.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == target.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target).model_copy(
        update={"blocked_reason": blocked}
    )
    encounter = distracted(
        runtime,
        state,
        encounter,
        target.actor_id,
        defended=defense is not None,
        injured=sum(injuries) > 0,
    )
    if updated.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == target.actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        second_defense=second_trace,
        attack_value=value,
        defense_value=defense_value_,
        damage_dice=tuple(damage_dice),
        basic_damage=sum(damages),
        resistance=first_dr,
        injury=sum(injuries),
        hp_before=hp.current,
        hp_after=updated.current,
        incapacitated=updated.injury.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_table,
        location=first_location,
        location_dice=first_location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
        adjudication_required=blocked,
        shots_fired=shots_fired,
        malfunction_table=malfunction_table,
        malfunction=failure.kind if failure is not None else None,
        hits=impacts,
        per_hit_damage=tuple(damages),
        per_hit_injury=tuple(injuries),
        per_hit_resistance=tuple(hit_resistances) if effective_shots > 1 else (),
        per_hit_locations=tuple(hit_locations) if effective_shots > 1 else (),
        per_hit_location_dice=tuple(hit_location_dice) if effective_shots > 1 else (),
    )
    if failure is not None:
        from wayfarer.engine.simulation.firearms import MalfunctionRecord, save_malfunction

        state = state.model_copy(
            update={
                "resources": save_malfunction(
                    state.resources,
                    MalfunctionRecord(
                        id=pending.id,
                        encounter_id=encounter.id,
                        attacker=actor,
                        defender=original_target,
                        attacker_build_revision=compiled.revision,
                        defender_build_revision=defender_build.revision,
                        catalog=equipment,
                        weapon=weapon,
                        scene=scene,
                        original_attack=original_attack,
                        ammunition_load=next(
                            (
                                v
                                for v in original_resources.ammunition_loads
                                if v.weapon_id == pending.weapon_id
                            ),
                            None,
                        ),
                        items=tuple(
                            i
                            for i in original_resources.items
                            if i.owner_id in (actor.actor_id, target.actor_id)
                        ),
                        pools=tuple(
                            p
                            for p in original_resources.pools
                            if p.id
                            in (
                                f"hp:{actor.actor_id}",
                                f"fp:{actor.actor_id}",
                                f"hp:{target.actor_id}",
                                f"fp:{target.actor_id}",
                            )
                        ),
                        failure=failure,
                        trace=trace,
                    ),
                )
            }
        )
    if (
        weapon.firearm
        and weapon.firearm.action == "single-use"
        and (shots_fired or failure and failure.kind == "dud")
    ):
        from wayfarer.engine.simulation.firearms import spend_rounds

        spent_resources = state.resources
        if failure and failure.kind == "dud":
            spent_resources = spend_rounds(spent_resources, pending.weapon_id, 1)
        spent = next(i for i in spent_resources.items if i.id == pending.weapon_id)
        state = state.model_copy(
            update={
                "resources": spent_resources.model_copy(
                    update={
                        "items": tuple(i for i in spent_resources.items if i.id != spent.id),
                        "expended_items": spent_resources.expended_items
                        + (
                            spent.model_copy(
                                update={"ready": False, "equipped": False, "container_id": None}
                            ),
                        ),
                    }
                )
            }
        )
    if critical_table:
        from wayfarer.engine.simulation.ranged_critical import RangedCritical, save_ranged_critical

        state = state.model_copy(
            update={
                "resources": save_ranged_critical(
                    state.resources,
                    RangedCritical(
                        id=pending.id,
                        encounter_id=encounter.id,
                        created_at=state.resources.game_time,
                        attacker=actor,
                        defender=original_target,
                        attacker_build_revision=compiled.revision,
                        defender_build_revision=defender_build.revision,
                        catalog=equipment,
                        weapon=weapon,
                        scene=scene,
                        ammunition_load=next(
                            (
                                load
                                for load in original_resources.ammunition_loads
                                if load.weapon_id == pending.weapon_id
                            ),
                            None,
                        ),
                        items=tuple(
                            i
                            for i in original_resources.items
                            if i.owner_id in (actor.actor_id, target.actor_id)
                        ),
                        pools=tuple(
                            p
                            for p in original_resources.pools
                            if p.id
                            in (
                                f"hp:{actor.actor_id}",
                                f"fp:{actor.actor_id}",
                                f"hp:{target.actor_id}",
                                f"fp:{target.actor_id}",
                            )
                        ),
                        trace=trace,
                        table_rolls=critical_rolls,
                        subject_id=target.actor_id if parry_item else actor.actor_id,
                        affected_item_id=parry_item or pending.weapon_id,
                        affected_mode_id=critical_parry_mode if parry_item else weapon.id,
                    ),
                )
            }
        )
    return state, encounter, trace
