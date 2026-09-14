"""One combat turn: declaring it, letting a Wait interrupt it, and applying it."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.tables.combat import (
    maneuver_move_allowance,
    maneuver_permission,
)
from wayfarer.engine.simulation.combat import maneuver_rules
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.close_combat import (
    enter as enter_close,
)
from wayfarer.engine.simulation.combat.close_combat import (
    leave as leave_close,
)
from wayfarer.engine.simulation.combat.close_combat import (
    remove_departed_pairs,
)
from wayfarer.engine.simulation.combat.encounter import (
    Combatant,
    CombatResult,
    Encounter,
    PendingDefense,
    basic_reachable,
    basic_visible,
    move_basic,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    CrouchAction,
    DefenseOption,
    WaitInterrupt,
    WaitTrigger,
)
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
)
from wayfarer.engine.simulation.combat.tactical import (
    attack_approach,
    move_hex,
    pop_up_hex,
    pose,
    sight,
)
from wayfarer.engine.simulation.combat.turn_commitment import prepare as prepare_commitment
from wayfarer.engine.simulation.combat.vocabulary import Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS, Hex, HexFacing, Pose
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import BasicMove
    from wayfarer.engine.simulation.combat.engine import CombatEngine


def _square_occupancy(encounter: Encounter, actor_id: str) -> tuple[set[GridPoint], set[GridPoint]]:
    occupied = {
        p.position
        for p in encounter.participants
        if p.actor_id != actor_id and isinstance(p.position, GridPoint)
    }
    blockers = {
        p.position
        for p in encounter.participants
        if p.actor_id != actor_id
        and isinstance(p.position, GridPoint)
        and encounter.blocks_passage(actor_id, p.actor_id)
    }
    return occupied, blockers


def _remember_hex_move(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    start: Pose | None,
    path: tuple[Hex, ...],
) -> tuple[Encounter, Combatant]:
    """Persist the immediately preceding mapped move for B371 relative velocity."""

    if encounter.spatial_kind != "hex":
        return encounter, participant
    assert start is not None
    last_direction = None
    if path:
        before_last = path[-2] if len(path) > 1 else start.position
        delta = (path[-1].q - before_last.q, path[-1].r - before_last.r)
        last_direction = DIRECTIONS.index(delta) if delta in DIRECTIONS else None
    participant = participant.model_copy(
        update={"last_hex_move": len(path), "last_hex_direction": last_direction}
    )
    return engine._replace(encounter, participant), participant


def _apply_crouch(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    maneuver: Maneuver,
    posture: Posture | None,
    crouch: CrouchAction | None,
) -> tuple[Encounter, Combatant]:
    """Validate and apply the free posture change that occurs before a maneuver."""
    if crouch is None:
        return encounter, participant
    if engine.rules.gurps_equipment is None:
        raise ValidationError("Crouching requires exact GURPS profile dispatch")
    if (maneuver == "change_posture" or posture is not None) and crouch != "rise":
        raise ValidationError("Crouching cannot compose a second posture action")
    if crouch == "after":
        if participant.posture != "standing":
            raise ValidationError("Only a standing combatant may crouch")
        if maneuver_permission(maneuver).movement not in ("none", "step"):
            raise ValidationError("Crouching after an action permits at most a step")
        return encounter, participant
    expected, changed = (
        ("standing", "crouching") if crouch == "before" else ("crouching", "standing")
    )
    if participant.posture != expected:
        raise ValidationError(
            "Only a standing combatant may crouch"
            if crouch == "before"
            else "Only a crouching combatant may rise freely"
        )
    participant = participant.model_copy(update={"posture": changed})
    return engine._replace(encounter, participant), participant


def _finish_crouch(participant: Combatant, crouch: CrouchAction | None) -> Combatant:
    return (
        participant.model_copy(update={"posture": "crouching"})
        if crouch == "after"
        else participant
    )


def _wait_interruption(
    engine: CombatEngine,
    original: Encounter,
    original_resources: ResourceState,
    result: tuple[Encounter, ResourceState, CombatResult],
    *,
    actor_id: str,
    maneuver: Maneuver,
    target_id: str | None,
    command_json: str,
    hex_path: tuple[Hex, ...],
    basic_move: BasicMove | None,
    crouch: CrouchAction | None,
) -> tuple[Encounter, ResourceState, CombatResult] | None:
    """Pause a completed declaration when a recorded Wait trigger matches it."""
    action = "attack" if maneuver in ATTACK_MANEUVERS else maneuver
    waiters = {p.actor_id: p for p in original.participants if p.actor_id != actor_id}
    for waiter_id in original.turn_order:
        waiter = waiters.get(waiter_id)
        trigger = waiter.maneuver_state.wait if waiter else None
        if trigger:
            assert waiter is not None
            before_actor = next(p for p in original.participants if p.actor_id == actor_id)
            after_actor = next(p for p in result[0].participants if p.actor_id == actor_id)
            path_points = hex_path or (
                (after_actor.position,) if isinstance(after_actor.position, Hex) else ()
            )
            trigger_index = (
                next(
                    (
                        index
                        for index, point in enumerate(path_points)
                        if not trigger.zone or (point.q, point.r) in trigger.zone
                    ),
                    None,
                )
                if path_points
                else None
            )
            zone_hit = trigger_index is not None or not path_points and not trigger.zone
            if (
                original.spatial_kind == "hex"
                and trigger.action == "move"
                and trigger_index is not None
                and hex_path
            ):
                after_actor = move_hex(
                    original,
                    before_actor,
                    maneuver,
                    hex_path[: trigger_index + 1],
                    None,
                    None,
                    board=engine.hex_map(original),
                )
            stop_candidate = trigger.stop_thrust and (
                original.spatial_kind != "basic"
                and action == "attack"
                and target_id == waiter_id
                and engine.distance(before_actor.position, after_actor.position) >= 1
                and engine.distance(after_actor.position, waiter.position)
                < engine.distance(before_actor.position, waiter.position)
            )
            if stop_candidate and waiter.reach <= before_actor.reach:
                raise ValidationError("Equal or shorter-reach stop-thrust ordering is unsupported")
            stop_thrust = stop_candidate and waiter.reach > before_actor.reach
            observable = True
            if original.spatial_kind == "hex":
                observed = engine._replace(result[0], after_actor)
                observable = sight(observed, waiter, after_actor, board=engine.hex_map(observed))
            elif original.spatial_kind == "basic":
                observable = basic_visible(original, waiter_id, actor_id)
            matches = (
                (trigger.actor_id is None or trigger.actor_id == actor_id)
                and trigger.action == action
                and (trigger.target_id is None or trigger.target_id == target_id)
                and zone_hit
                and observable
                and (not trigger.stop_thrust or stop_thrust)
            )
        else:
            matches = False
        if not matches:
            continue
        assert waiter is not None and trigger is not None
        bonus = (
            engine.distance(before_actor.position, after_actor.position) // 2
            if trigger.stop_thrust
            else 0
        )
        moved = (
            basic_move is not None
            if original.spatial_kind == "basic"
            else engine.distance(before_actor.position, after_actor.position) >= 1
        )
        pose_changed = moved or crouch in ("before", "rise")
        paused = engine._replace(
            original,
            waiter.model_copy(
                update={
                    "maneuver_state": waiter.maneuver_state.model_copy(
                        update={"wait": None, "stop_thrust_damage_bonus": bonus}
                    )
                }
            ),
        )
        resume_json = command_json
        if pose_changed:
            if original.spatial_kind == "basic":
                if moved:
                    paused = paused.model_copy(update={"spatial_context": result[0].spatial})
                paused = engine._replace(
                    paused, before_actor.model_copy(update={"posture": after_actor.posture})
                )
            else:
                paused = engine._replace(
                    paused,
                    before_actor.model_copy(
                        update={
                            "position": after_actor.position,
                            "facing": after_actor.facing,
                            "hex_facing": after_actor.hex_facing,
                            "posture": after_actor.posture,
                        }
                    ),
                )
            saved = json.loads(command_json)
            remaining_hex_path = (
                [point.model_dump(mode="json") for point in hex_path[trigger_index + 1 :]]
                if trigger.action == "move" and trigger_index is not None
                else []
            )
            saved.update(
                {
                    "destination": None,
                    "facing": None,
                    "posture": None,
                    "hex_path": remaining_hex_path,
                    "hex_facing": saved.get("hex_facing") if remaining_hex_path else None,
                    "basic_move": None,
                    "step_timing": "before",
                    "crouch": None if crouch in ("before", "rise") else crouch,
                }
            )
            if maneuver == "move" and not remaining_hex_path:
                saved["maneuver"] = "do_nothing"
            resume_json = json.dumps(saved, sort_keys=True, separators=(",", ":"))
        paused = paused.model_copy(
            update={
                "wait_interrupt": WaitInterrupt(
                    waiter_id=waiter_id,
                    actor_id=actor_id,
                    turn_index=original.turn_index,
                    command_json=resume_json,
                    declaration=trigger,
                )
            }
        )
        return (
            paused,
            original_resources,
            CombatResult(
                encounter_id=paused.id,
                code="combat.wait_triggered",
                round=paused.round,
                current_actor_id=actor_id,
                available=engine.available(paused, waiter_id),
            ),
        )
    return None


def take_turn(
    engine: CombatEngine,
    encounter: Encounter,
    *,
    actor_id: str,
    maneuver: Maneuver,
    resources: ResourceState,
    destination: GridPoint | None = None,
    facing: Facing | None = None,
    posture: Posture | None = None,
    crouch: CrouchAction | None = None,
    item_id: str | None = None,
    target_id: str | None = None,
    command_id: str,
    attack_option: AttackOption | None = None,
    defense_option: DefenseOption | None = None,
    wait_trigger: WaitTrigger | None = None,
    step_timing: Literal["before", "after"] = "before",
    second_item_id: str | None = None,
    second_target_id: str | None = None,
    second_mode_id: str | None = None,
    command_json: str = "",
    hex_path: tuple[Hex, ...] = (),
    hex_facing: HexFacing | None = None,
    pop_up: bool = False,
    enter_high_speed: bool = False,
    basic_move: BasicMove | None = None,
    spatial_revision: int | None = None,
    suppression_fire: bool = False,
    enter_close_combat: bool = False,
    shield_rush: bool = False,
) -> tuple[Encounter, ResourceState, CombatResult]:
    original, original_resources = encounter, resources
    interrupt = encounter.wait_interrupt
    if interrupt is not None:
        if interrupt.ready or interrupt.reacting or actor_id != interrupt.waiter_id:
            raise ConflictError("Resolve the interrupted Wait before another action")
        declaration = interrupt.declaration
        reacting_waiter = next(p for p in encounter.participants if p.actor_id == actor_id)
        if declaration.reaction == "all_out_attack" and reacting_waiter.maneuver_state.defended:
            declaration = declaration.model_copy(
                update={"reaction": "attack", "attack_option": None}
            )
        if declaration.unarmed is not None and maneuver != "do_nothing":
            raise ValidationError("The declared Wait reaction is an unarmed attack")
        if maneuver != "do_nothing" and (maneuver, item_id, target_id, attack_option) != (
            declaration.reaction,
            declaration.item_id,
            declaration.reaction_target_id,
            declaration.attack_option,
        ):
            raise ValidationError("Wait reaction must match its recorded declaration")
        if maneuver != "do_nothing" and (
            step_timing != "before"
            or any(v is not None for v in (second_item_id, second_target_id, second_mode_id))
        ):
            raise ValidationError("Wait reaction includes undeclared maneuver options")
        encounter = encounter.model_copy(
            update={
                "turn_index": encounter.turn_order.index(actor_id),
                "wait_interrupt": interrupt.model_copy(update={"reacting": True}),
            }
        )
    result = engine._take_turn(
        encounter,
        actor_id=actor_id,
        maneuver=maneuver,
        resources=resources,
        spatial_revision=spatial_revision or original_resources.revision + 1,
        destination=destination,
        facing=facing,
        posture=posture,
        crouch=crouch,
        item_id=item_id,
        target_id=target_id,
        command_id=command_id,
        attack_option=attack_option,
        defense_option=defense_option,
        wait_trigger=wait_trigger,
        step_timing=step_timing,
        second_item_id=second_item_id,
        second_target_id=second_target_id,
        second_mode_id=second_mode_id,
        hex_path=hex_path,
        hex_facing=hex_facing,
        pop_up=pop_up,
        enter_high_speed=enter_high_speed,
        basic_move=basic_move,
        suppression_fire=suppression_fire,
        enter_close_combat=enter_close_combat,
        shield_rush=shield_rush,
    )
    if engine.rules.gurps_equipment is not None and interrupt is None and command_json:
        interrupted = _wait_interruption(
            engine,
            original,
            original_resources,
            result,
            actor_id=actor_id,
            maneuver=maneuver,
            target_id=target_id,
            command_json=command_json,
            hex_path=hex_path,
            basic_move=basic_move,
            crouch=crouch,
        )
        if interrupted is not None:
            return interrupted
    return result


def _apply_hex_movement(
    engine: CombatEngine,
    encounter: Encounter,
    participant: Combatant,
    *,
    maneuver: Maneuver,
    destination: GridPoint | None,
    facing: Facing | None,
    posture: Posture | None,
    crouch: CrouchAction | None,
    target_id: str | None,
    defense_option: DefenseOption | None,
    hex_path: tuple[Hex, ...],
    hex_facing: HexFacing | None,
    pop_up: bool,
    enter_high_speed: bool,
    basic_move: BasicMove | None,
    enter_close_combat: bool,
) -> tuple[Combatant, Pose | None]:
    """Select one mapped movement transaction without growing turn dispatch."""
    if pop_up:
        if enter_high_speed:
            raise ValidationError("Pop-up attacks cannot enter high-speed movement")
        if (
            enter_close_combat
            or maneuver != "attack"
            or posture is not None
            or crouch is not None
            or destination is not None
            or facing is not None
            or basic_move is not None
        ):
            raise ValidationError("Pop-up attack requires one mapped Attack transaction")
        target = next((p for p in encounter.participants if p.actor_id == target_id), None)
        if target is None or sight(encounter, participant, target, board=engine.hex_map(encounter)):
            raise ValidationError("Pop-up attack must begin out of sight behind cover")
        return pop_up_hex(
            encounter,
            participant,
            hex_path,
            hex_facing,
            board=engine.hex_map(encounter),
        )
    if destination is not None or facing is not None:
        raise ValidationError("Hex encounters require explicit hex paths and facings")
    if posture is not None and hex_path:
        raise ValidationError("A posture step cannot also translate the actor")
    return (
        move_hex(
            encounter,
            participant,
            maneuver,
            hex_path,
            hex_facing,
            defense_option,
            board=engine.hex_map(encounter),
            enter_high_speed=enter_high_speed,
            enter_close_combat=enter_close_combat,
        ),
        None,
    )


def _validate_close_entry(
    encounter: Encounter,
    maneuver: Maneuver,
    target_id: str | None,
    hex_path: tuple[Hex, ...],
    pop_up: bool,
    step_timing: Literal["before", "after"],
    enter_close_combat: bool,
) -> None:
    if enter_close_combat and (
        encounter.spatial_kind != "hex"
        or target_id is None
        or not hex_path
        or pop_up
        or step_timing == "after"
        or maneuver not in ("move", "attack", "move_and_attack", "all_out_attack")
    ):
        raise ValidationError("Close-combat entry requires a legal mapped maneuver and target")


def _validate_high_speed(
    encounter: Encounter,
    participant: Combatant,
    actor_id: str,
    enter_high_speed: bool,
) -> None:
    if enter_high_speed and encounter.spatial_kind != "hex":
        raise ValidationError("High-speed movement requires an exact hex battlefield")
    if (enter_high_speed or participant.high_speed is not None) and any(
        combatant.maneuver_state.wait is not None
        for combatant in encounter.participants
        if combatant.actor_id != actor_id
    ):
        raise ValidationError("High-speed movement with an active Wait is not yet supported")


def apply_turn(
    engine: CombatEngine,
    encounter: Encounter,
    *,
    actor_id: str,
    maneuver: Maneuver,
    resources: ResourceState,
    spatial_revision: int,
    command_id: str,
    destination: GridPoint | None = None,
    facing: Facing | None = None,
    posture: Posture | None = None,
    crouch: CrouchAction | None = None,
    item_id: str | None = None,
    target_id: str | None = None,
    attack_option: AttackOption | None = None,
    defense_option: DefenseOption | None = None,
    wait_trigger: WaitTrigger | None = None,
    step_timing: Literal["before", "after"] = "before",
    second_item_id: str | None = None,
    second_target_id: str | None = None,
    second_mode_id: str | None = None,
    hex_path: tuple[Hex, ...] = (),
    hex_facing: HexFacing | None = None,
    pop_up: bool = False,
    enter_high_speed: bool = False,
    basic_move: BasicMove | None = None,
    suppression_fire: bool = False,
    enter_close_combat: bool = False,
    shield_rush: bool = False,
) -> tuple[Encounter, ResourceState, CombatResult]:
    if engine.rules.gurps_equipment is not None:
        command_id = "combat:" + hashlib.sha256(command_id.encode()).hexdigest()
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Encounter cannot accept a maneuver now")
    if actor_id != encounter.current_actor_id:
        raise ConflictError("Combat action is out of turn")
    if maneuver == "concentrate" and engine.rules.gurps_equipment is None:
        raise ValidationError("Concentration requires a bound ability command")
    participant = next(p for p in encounter.participants if p.actor_id == actor_id)
    _validate_high_speed(encounter, participant, actor_id, enter_high_speed)
    _validate_close_entry(
        encounter,
        maneuver,
        target_id,
        hex_path,
        pop_up,
        step_timing,
        enter_close_combat,
    )
    if encounter.spatial_kind == "hex" and hex_path and not enter_close_combat:
        leave_close(encounter, participant, hex_path)
    encounter, participant = _apply_crouch(
        engine, encounter, participant, maneuver, posture, crouch
    )
    start_position = (
        None if isinstance(encounter.spatial, BasicSpatialContext) else participant.position
    )
    start_hex_pose = pose(participant) if encounter.spatial_kind == "hex" else None
    tactical_attack_pose = None
    # B205: a stream lasts only while its holder keeps pouring it on the same
    # weapon and mode. Any other maneuver lets go of it (#359).
    if participant.stream is not None and not (
        maneuver in ATTACK_MANEUVERS and item_id == participant.stream.weapon_id
    ):
        participant = participant.model_copy(update={"stream": None})
        encounter = engine._replace(encounter, participant)
    basic = isinstance(encounter.spatial, BasicSpatialContext)
    battlefield = None if basic else engine.battlefields[encounter.battlefield_id]
    deferred_step = step_timing == "after"
    if deferred_step and (maneuver != "attack" or pop_up):
        raise ValidationError("Only an ordinary Attack permits a step after the attack")
    if deferred_step and not (
        destination is not None
        or hex_path
        or hex_facing is not None
        or posture is not None
        or basic_move is not None
        or crouch == "after"
    ):
        raise ValidationError("A post-attack step requires movement, facing, or posture")
    if deferred_step and encounter.spatial_kind == "hex":
        if destination is not None or facing is not None:
            raise ValidationError("Hex encounters require explicit hex paths and facings")
        if posture is not None and (hex_path or hex_facing is not None):
            raise ValidationError("A posture step cannot also move or turn")
        if posture is not None and {posture, participant.posture} != {
            "standing",
            "kneeling",
        }:
            raise ValidationError("A posture step only switches standing and kneeling")
        if posture is None:
            move_hex(
                encounter,
                participant,
                "attack",
                hex_path,
                hex_facing,
                None,
                board=engine.hex_map(encounter),
            )
    elif deferred_step and destination is not None:
        assert battlefield is not None
        if not isinstance(participant.position, GridPoint):
            raise ValidationError("Square step requires square coordinates")
        occupied, blockers = _square_occupancy(encounter, actor_id)
        if destination in occupied:
            raise ValidationError("Post-attack step cannot end in an occupied position")
        limit = maneuver_move_allowance(
            "attack", participant.movement_allowance, participant.posture
        )
        if not engine._reachable(battlefield, participant.position, destination, limit, blockers):
            raise ValidationError("Post-attack step exceeds allowance or terrain constraints")
    elif (
        deferred_step
        and posture is not None
        and {
            posture,
            participant.posture,
        }
        != {"standing", "kneeling"}
    ):
        raise ValidationError("A posture step only switches standing and kneeling")
    if encounter.spatial_kind == "hex" and not deferred_step:
        participant, tactical_attack_pose = _apply_hex_movement(
            engine,
            encounter,
            participant,
            maneuver=maneuver,
            destination=destination,
            facing=facing,
            posture=posture,
            crouch=crouch,
            target_id=target_id,
            defense_option=defense_option,
            hex_path=hex_path,
            hex_facing=hex_facing,
            pop_up=pop_up,
            enter_high_speed=enter_high_speed,
            basic_move=basic_move,
            enter_close_combat=enter_close_combat,
        )
        encounter = engine._replace(encounter, participant)
        if enter_close_combat:
            encounter = enter_close(encounter, participant, target_id or "")
        else:
            encounter = remove_departed_pairs(encounter, actor_id)
    elif (hex_path or hex_facing is not None) and not deferred_step:
        raise ValidationError("Hex movement requires explicit battlefield migration")
    if basic_move is not None:
        if not basic or destination is not None or facing is not None or hex_path or hex_facing:
            raise ValidationError("Basic movement cannot mix mapped movement fields")
        if maneuver_permission(maneuver).movement in ("none", "triggered"):
            raise ValidationError("Maneuver does not permit basic movement")
        if maneuver in ("attack", "aim", "evaluate", "feint"):
            raise ValidationError(
                "Basic movement with a spatially dependent maneuver requires "
                "post-movement GM adjudication"
            )
        if not deferred_step:
            allowance = (
                engine.rules.prone_movement_allowance
                if engine.rules.gurps_equipment is None
                and maneuver == "move"
                and participant.posture == "prone"
                else maneuver_move_allowance(
                    maneuver,
                    participant.movement_allowance,
                    participant.posture,
                    increased_dodge=(maneuver == "all_out_defense" and defense_option == "dodge"),
                )
            )
            encounter = move_basic(
                encounter,
                actor_id=actor_id,
                reference_actor_id=basic_move.reference_actor_id,
                direction=basic_move.direction,
                yards=allowance,
                command_id=command_id,
                revision=spatial_revision,
            )
    if engine.rules.gurps_equipment is None and (
        maneuver not in ("do_nothing", "move", "ready", "change_posture", "attack", "wait")
        or attack_option
        or defense_option
        or wait_trigger
    ):
        raise ValidationError("Maneuver requires exact GURPS profile dispatch")
    if (
        (attack_option is not None and maneuver != "all_out_attack")
        or (defense_option is not None and maneuver != "all_out_defense")
        or (wait_trigger is not None and maneuver != "wait")
    ):
        raise ValidationError("Maneuver options do not match the maneuver")
    if step_timing == "after" and maneuver != "attack":
        raise ValidationError("Post-attack movement requires Attack")
    if engine.rules.gurps_equipment is not None:
        encounter, participant = _remember_hex_move(
            engine, encounter, participant, start_hex_pose, hex_path
        )
        participant = prepare_commitment(
            engine,
            encounter,
            participant,
            resources,
            actor_id=actor_id,
            maneuver=maneuver,
            item_id=item_id,
            target_id=target_id,
            attack_option=attack_option,
            defense_option=defense_option,
            wait_trigger=wait_trigger,
            second_item_id=second_item_id,
            second_target_id=second_target_id,
            second_mode_id=second_mode_id,
            basic=basic,
        )
        permits_step = maneuver_permission(maneuver).movement == "step"
        if facing is not None and permits_step:
            participant = participant.model_copy(update={"facing": facing})
            facing = None
        if posture is not None and permits_step:
            if destination is not None or {posture, participant.posture} != {
                "standing",
                "kneeling",
            }:
                raise ValidationError("A posture step only switches standing and kneeling in place")
            participant = participant.model_copy(update={"posture": posture})
            posture = None
        # A single destination is one movement allowance, never a second action.
        if destination is not None and maneuver != "move" and not deferred_step:
            permission = maneuver_permission(maneuver)
            if permission.movement in ("none", "triggered"):
                raise ValidationError("Maneuver does not permit a step")
            limit = maneuver_move_allowance(
                maneuver,
                participant.movement_allowance,
                participant.posture,
                increased_dodge=maneuver == "all_out_defense" and defense_option == "dodge",
            )
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square movement requires square coordinates")
            occupied, blockers = _square_occupancy(encounter, actor_id)
            if destination in occupied:
                raise ValidationError("Movement cannot end in an occupied position")
            assert battlefield is not None
            if not engine._reachable(
                battlefield,
                participant.position,
                destination,
                limit,
                blockers,
            ):
                raise ValidationError("Maneuver movement exceeds allowance or terrain constraints")
            if maneuver == "all_out_attack":
                dx, dy = (
                    destination.x - participant.position.x,
                    destination.y - participant.position.y,
                )
                forward = {
                    "north": dy < 0 and dx == 0,
                    "south": dy > 0 and dx == 0,
                    "east": dx > 0 and dy == 0,
                    "west": dx < 0 and dy == 0,
                }
                if destination != participant.position and not forward[participant.facing]:
                    raise ValidationError("All-Out Attack movement must be forward")
            participant = participant.model_copy(update={"position": destination})
            destination = None
    selected = maneuver_rules.kind(
        maneuver,
        spatial_kind=encounter.spatial_kind,
        basic=basic,
        suppression_fire=suppression_fire,
        attack_maneuvers=ATTACK_MANEUVERS,
    )
    declared = maneuver_rules.Declaration(
        engine=engine,
        encounter=encounter,
        participant=participant,
        resources=resources,
        actor_id=actor_id,
        maneuver=maneuver,
        command_id=command_id,
        battlefield=battlefield,
        basic=basic,
        deferred_step=deferred_step,
        destination=destination,
        facing=facing,
        posture=posture,
        item_id=item_id,
        target_id=target_id,
        basic_move=basic_move,
        hex_path=hex_path,
        enter_close_combat=enter_close_combat,
    )
    if selected == "attack":
        if (
            target_id is None
            or item_id is None
            or (
                not deferred_step
                and any(value is not None for value in (destination, facing, posture))
            )
        ):
            raise ValidationError("Attack intent requires a target and ready weapon")
        target = next((p for p in encounter.participants if p.actor_id == target_id), None)
        if (
            target is None
            or target.actor_id == actor_id
            or item_id not in participant.ready_item_ids
            or (
                not basic_reachable(encounter, actor_id, target_id)
                and not any(
                    s.attacker_id == actor_id and s.defender_id == target_id
                    for s in encounter.ranged_situations
                )
            )
        ):
            raise ValidationError("Attack target or weapon is unavailable or out of reach")
        participant = participant.model_copy(
            update={"last_maneuver": maneuver, "last_attack_item_id": item_id}
        )
        encounter = engine._replace(encounter, participant)
        pending = PendingDefense(
            id=("defense:" + hashlib.sha256(command_id.encode()).hexdigest())
            if engine.rules.gurps_equipment
            else f"{command_id}:defense",
            attacker_id=actor_id,
            defender_id=target_id,
            weapon_id=item_id,
            allowed=("dodge", "parry", "none") if target.ready_item_ids else ("dodge", "none"),
            opened_round=encounter.round,
            opened_turn=encounter.turn_index,
            shield_rush=shield_rush,
            collision_velocity=(
                len(hex_path)
                + (
                    target.last_hex_move
                    if shield_rush
                    and participant.last_hex_direction is not None
                    and target.last_hex_direction
                    == (participant.last_hex_direction + 3) % 6
                    else 0
                )
                if shield_rush
                else 0
            ),
            tactical_approach=(
                "pop-up"
                if pop_up
                else "runaround"
                if start_hex_pose is not None
                and attack_approach(pose(target), pose(participant), origin=start_hex_pose)
                == "runaround"
                else None
            ),
            tactical_attack_pose=tactical_attack_pose,
            post_attack_destination=destination if deferred_step else None,
            post_attack_square_facing=facing if deferred_step else None,
            post_attack_hex_path=hex_path if deferred_step else (),
            post_attack_facing=hex_facing if deferred_step else None,
            post_attack_posture=posture if deferred_step else None,
            post_attack_crouch=crouch == "after",
            post_attack_basic_reference_id=(
                basic_move.reference_actor_id if deferred_step and basic_move else None
            ),
            post_attack_basic_direction=(
                basic_move.direction if deferred_step and basic_move else None
            ),
        )
        encounter = encounter.model_copy(update={"pending_defense": pending})
        return (
            encounter,
            resources,
            CombatResult(
                encounter_id=encounter.id,
                code="combat.defense_required",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                pending_defense_id=pending.id,
                available=tuple(pending.allowed),
            ),
        )
    else:
        participant, resources = maneuver_rules.RULES[selected](declared)
    participant = _finish_crouch(participant, crouch)
    encounter = engine._replace(encounter, participant)
    movement_path: tuple[Hex, ...] = ()
    if isinstance(start_position, Hex):
        after = next(p for p in encounter.participants if p.actor_id == actor_id).position
        movement_path = hex_path or (
            (after,) if isinstance(after, Hex) and after != start_position else ()
        )
    if isinstance(start_position, Hex) and movement_path:
        encounter = engine._suppression_attacks(
            encounter,
            actor_id=actor_id,
            before=start_position,
            path=movement_path,
            command_id=command_id,
        )
    if encounter.pending_defense is not None:
        pending = encounter.pending_defense
        return (
            encounter,
            resources,
            CombatResult(
                encounter_id=encounter.id,
                code="combat.defense_required",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                pending_defense_id=pending.id,
                available=tuple(pending.allowed),
            ),
        )
    encounter = engine._advance(encounter)
    return (
        encounter,
        resources,
        CombatResult(
            encounter_id=encounter.id,
            code=f"combat.{maneuver}",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=engine.available(encounter, encounter.current_actor_id),
        ),
    )
