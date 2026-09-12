"""What the shooter faces: declaring a shot and the facts it depends on."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, RangedSituation
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.combat.ranged.ammunition import reload_weapon, unload_weapon
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def declare(
    runtime: RulesContext, encounter: Encounter, situations: tuple[RangedSituation, ...]
) -> Encounter:
    if not situations:
        return encounter
    if runtime.rules.combat and runtime.rules.combat.gurps_equipment is not None:
        from wayfarer.engine.simulation.actors import catalog

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
        from wayfarer.engine.simulation.combat.tactical import attack_geometry, pose
        from wayfarer.engine.simulation.hex_geometry import ranged_distance

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
        from wayfarer.engine.simulation.combat.encounter import basic_distance

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
        from wayfarer.engine.simulation.combat.melee.modes import mode

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
        from wayfarer.engine.simulation.combat.thrown.items import recover

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
        from wayfarer.engine.simulation.combat.unarmed.declaration import declare_unarmed_wait

        declare_unarmed_wait(runtime, state, encounter, command.actor_id, command.wait_trigger)
    if command.wait_trigger is not None and command.wait_trigger.stop_thrust:
        from wayfarer.engine.simulation.combat.melee.modes import mode
        from wayfarer.engine.simulation.equipment.catalog import MeleeMode

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
        from wayfarer.engine.simulation.combat.firearm_transitions import service

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
        from wayfarer.engine.simulation.combat.melee.modes import mode
        from wayfarer.engine.simulation.combat.ranged_readiness import let_down

        selected = mode(runtime, state, command.actor_id, command.item_id or "", command.mode_id)
        if not isinstance(selected, RangedMode):
            raise ValidationError("Let-down requires a bow mode")
        let_down(state, command, selected)
    if command.reload_ammunition_id is not None:
        if command.maneuver != "ready":
            raise ValidationError("Reload requires a Ready maneuver")
        reload_weapon(runtime, state, command, validate_only=True)


def validate_rated_strength(profile_id: str, weapon: RangedMode, st: int) -> None:
    if weapon.rated_strength is None:
        return
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Rated weapon ST requires the exact Basic Set profile")
    if weapon.rated_strength.kind == "bow" and weapon.rated_strength.st > st:
        raise ValidationError("Bow ST exceeds the wielder's effective ST")
