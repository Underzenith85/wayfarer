"""One combat turn: declaring it, letting a Wait interrupt it, and applying it."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.simulation.combat import maneuver_rules
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import (
    CombatResult,
    Encounter,
    PendingDefense,
    basic_distance,
    basic_reachable,
    basic_visible,
    move_basic,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    DefenseOption,
    ManeuverState,
    WaitInterrupt,
    WaitTrigger,
)
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
)
from wayfarer.engine.simulation.combat.tactical import move_hex, sight
from wayfarer.engine.simulation.combat.vocabulary import Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexFacing
from wayfarer.engine.simulation.magic.spells import active_spells
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import BasicMove
    from wayfarer.engine.simulation.combat.engine import CombatEngine


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
    basic_move: BasicMove | None = None,
    spatial_revision: int | None = None,
    suppression_fire: bool = False,
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
        basic_move=basic_move,
        suppression_fire=suppression_fire,
    )
    if engine.rules.gurps_equipment is not None and interrupt is None and command_json:
        action = "attack" if maneuver in ATTACK_MANEUVERS else maneuver
        waiters = {p.actor_id: p for p in original.participants if p.actor_id != actor_id}
        for waiter_id in original.turn_order:
            waiter = waiters.get(waiter_id)
            trigger = waiter.maneuver_state.wait if waiter else None
            if trigger:
                assert waiter is not None
                before_actor = next(p for p in original.participants if p.actor_id == actor_id)
                after_actor = next(p for p in result[0].participants if p.actor_id == actor_id)
                zone_hit = not trigger.zone or any(
                    (point.q, point.r) in trigger.zone
                    for point in (
                        hex_path
                        or (
                            (after_actor.position,) if isinstance(after_actor.position, Hex) else ()
                        )
                    )
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
                    raise ValidationError(
                        "Equal or shorter-reach stop-thrust ordering is unsupported"
                    )
                stop_thrust = stop_candidate and waiter.reach > before_actor.reach
                observable = True
                if original.spatial_kind == "hex":
                    observable = sight(
                        result[0], waiter, after_actor, board=engine.hex_map(result[0])
                    )
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
            if matches:
                assert waiter is not None and trigger is not None
                declaration = trigger
                bonus = (
                    engine.distance(before_actor.position, after_actor.position) // 2
                    if declaration.stop_thrust
                    else 0
                )
                moved = (
                    basic_move is not None
                    if original.spatial_kind == "basic"
                    else engine.distance(before_actor.position, after_actor.position) >= 1
                )
                paused = engine._replace(
                    original,
                    waiter.model_copy(
                        update={
                            "maneuver_state": waiter.maneuver_state.model_copy(
                                update={
                                    "wait": None,
                                    "stop_thrust_damage_bonus": bonus,
                                }
                            )
                        }
                    ),
                )
                resume_json = command_json
                if moved:
                    if original.spatial_kind == "basic":
                        paused = paused.model_copy(update={"spatial_context": result[0].spatial})
                    else:
                        paused_actor = before_actor.model_copy(
                            update={
                                "position": after_actor.position,
                                "facing": after_actor.facing,
                                "hex_facing": after_actor.hex_facing,
                                "posture": after_actor.posture,
                            }
                        )
                        paused = engine._replace(paused, paused_actor)
                    saved = json.loads(command_json)
                    saved.update(
                        {
                            "destination": None,
                            "facing": None,
                            "posture": None,
                            "hex_path": [],
                            "hex_facing": None,
                            "basic_move": None,
                            "step_timing": "before",
                        }
                    )
                    if maneuver == "move":
                        saved["maneuver"] = "do_nothing"
                    resume_json = json.dumps(saved, sort_keys=True, separators=(",", ":"))
                paused = paused.model_copy(
                    update={
                        "wait_interrupt": WaitInterrupt(
                            waiter_id=waiter_id,
                            actor_id=actor_id,
                            turn_index=original.turn_index,
                            command_json=resume_json,
                            declaration=declaration,
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
    return result


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
    basic_move: BasicMove | None = None,
    suppression_fire: bool = False,
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
    start_position = (
        None if isinstance(encounter.spatial, BasicSpatialContext) else participant.position
    )
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
    if deferred_step and maneuver != "attack":
        raise ValidationError("Only Attack permits a step after the attack")
    if deferred_step and not (
        destination is not None
        or hex_path
        or hex_facing is not None
        or posture is not None
        or basic_move is not None
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
        occupied = {
            p.position
            for p in encounter.participants
            if p.actor_id != actor_id and isinstance(p.position, GridPoint)
        }
        limit = max(1, (participant.movement_allowance + 9) // 10)
        if not engine._reachable(battlefield, participant.position, destination, limit, occupied):
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
        if destination is not None or facing is not None:
            raise ValidationError("Hex encounters require explicit hex paths and facings")
        if posture is not None and hex_path:
            raise ValidationError("A posture step cannot also translate the actor")
        participant = move_hex(
            encounter,
            participant,
            maneuver,
            hex_path,
            hex_facing,
            defense_option,
            board=engine.hex_map(encounter),
        )
        encounter = engine._replace(encounter, participant)
    elif (hex_path or hex_facing is not None) and not deferred_step:
        raise ValidationError("Hex movement requires explicit battlefield migration")
    if basic_move is not None:
        if not basic or destination is not None or facing is not None or hex_path or hex_facing:
            raise ValidationError("Basic movement cannot mix mapped movement fields")
        allowed_steps = {
            "attack",
            "aim",
            "evaluate",
            "feint",
            "ready",
            "concentrate",
            "all_out_defense",
        }
        if maneuver != "move" and maneuver not in allowed_steps:
            raise ValidationError("Maneuver does not permit basic movement")
        if maneuver in ("attack", "aim", "evaluate", "feint"):
            raise ValidationError(
                "Basic movement with a spatially dependent maneuver requires "
                "post-movement GM adjudication"
            )
        if not deferred_step:
            allowance = (
                engine.rules.prone_movement_allowance
                if maneuver == "move" and participant.posture == "prone"
                else participant.movement_allowance
                if maneuver == "move"
                else max(1, (participant.movement_allowance + 9) // 10)
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
        old = participant.maneuver_state
        if participant.last_maneuver != "evaluate":
            old = old.model_copy(update={"evaluate_target_id": None, "evaluate_bonus": 0})
        if participant.last_maneuver != "feint":
            old = old.model_copy(update={"feint_target_id": None, "feint_penalty": 0})
        commitment = ManeuverState()
        if maneuver in ATTACK_MANEUVERS or maneuver == "feint":
            commitment = commitment.model_copy(
                update={
                    "aim_item_id": old.aim_item_id,
                    "aim_target_id": old.aim_target_id,
                    "aim_mode_id": old.aim_mode_id,
                    "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                    "aim_accuracy": old.aim_accuracy,
                    "aim_braced": old.aim_braced,
                    "aim_sight_bonus": old.aim_sight_bonus,
                    "evaluate_target_id": old.evaluate_target_id,
                    "evaluate_bonus": old.evaluate_bonus,
                    "feint_target_id": old.feint_target_id,
                    "feint_penalty": old.feint_penalty,
                    "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                }
            )
        if maneuver == "all_out_attack":
            if attack_option is None:
                raise ValidationError("Choose an All-Out Attack option")
            commitment = commitment.model_copy(
                update={
                    "defense_forbidden": True,
                    "attack_bonus": 4 if attack_option == "determined" else 0,
                    "strong": attack_option == "strong",
                    "attacks_remaining": int(attack_option == "double"),
                    "second_attack_item_id": second_item_id,
                    "second_attack_target_id": second_target_id,
                    "second_attack_mode_id": second_mode_id,
                }
            )
            if attack_option == "double":
                if second_target_id not in (None, target_id):
                    raise ValidationError("All-Out Attack (Double) attacks the same foe")
                if second_item_id is not None:
                    hands = {item: hand for item, hand in participant.hand_bindings}
                    if (
                        second_item_id == item_id
                        or second_item_id not in participant.ready_item_ids
                        or item_id not in hands
                        or second_item_id not in hands
                        or hands[item_id] == hands[second_item_id]
                    ):
                        raise ValidationError("Double requires two distinct ready one-hand weapons")
                    commitment = commitment.model_copy(
                        update={
                            "second_attack_target_id": target_id,
                            "attack_bonus": -4 if hands[item_id] == "left-hand" else 0,
                            "second_attack_penalty": -4
                            if hands[second_item_id] == "left-hand"
                            else 0,
                        }
                    )
            elif any(v is not None for v in (second_item_id, second_target_id, second_mode_id)):
                raise ValidationError("Second attack choices require All-Out Attack (Double)")
        if maneuver == "move_and_attack":
            commitment = commitment.model_copy(
                update={"attack_bonus": -4, "attack_cap": 9, "parry_forbidden": True}
            )
        if maneuver in ATTACK_MANEUVERS:
            commitment = commitment.model_copy(
                update={
                    "aim_item_id": old.aim_item_id,
                    "aim_target_id": old.aim_target_id,
                    "aim_mode_id": old.aim_mode_id,
                    "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                    "aim_accuracy": old.aim_accuracy,
                    "aim_braced": old.aim_braced,
                    "aim_sight_bonus": old.aim_sight_bonus,
                    "evaluate_target_id": old.evaluate_target_id,
                    "evaluate_bonus": old.evaluate_bonus,
                    "feint_target_id": old.feint_target_id,
                    "feint_penalty": old.feint_penalty,
                    "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                }
            )
        if maneuver == "concentrate":
            commitment = commitment.model_copy(
                update={
                    "concentrating": True,
                    "concentration_seconds": old.concentration_seconds + 1
                    if old.concentrating
                    else 1,
                }
            )
        if maneuver == "all_out_defense":
            if defense_option is None:
                raise ValidationError("Choose the enhanced active defense")
            commitment = commitment.model_copy(update={"enhanced_defense": defense_option})
        if maneuver == "wait":
            if (
                wait_trigger is None
                or wait_trigger.actor_id == actor_id
                or (
                    wait_trigger.actor_id is not None
                    and wait_trigger.actor_id not in encounter.turn_order
                )
            ):
                raise ValidationError("Wait requires an observable other combatant trigger")

            held_missile = any(
                effect.actor_id == actor_id
                and effect.spell_id == "fireball"
                and effect.execute_effects
                and wait_trigger.item_id
                == "spell:" + hashlib.sha256(effect.cast_id.encode()).hexdigest()
                for effect in active_spells(resources)
            )
            if held_missile and (
                wait_trigger.reaction != "attack" or wait_trigger.mode_id is not None
            ):
                raise ValidationError("Held missile Wait supports its declared release only")
            if (
                wait_trigger.unarmed is None
                and wait_trigger.item_id not in participant.ready_item_ids
                and wait_trigger.reaction != "ready"
                and not held_missile
            ):
                raise ValidationError("Wait attack requires a ready weapon")
            if (
                wait_trigger.reaction != "ready"
                and wait_trigger.reaction_target_id not in encounter.turn_order
            ):
                raise ValidationError("Wait attack requires a declared target")
            if (wait_trigger.reaction == "all_out_attack") != (
                wait_trigger.attack_option is not None
            ):
                raise ValidationError("Wait All-Out Attack requires its option in advance")
            if wait_trigger.zone:
                if encounter.spatial_kind != "hex":
                    raise ValidationError("Wait zones require an explicit hex battlefield")
                cells = {
                    (cell.position.q, cell.position.r)
                    for cell in engine.require_hex(encounter).cells
                }
                if not set(wait_trigger.zone) <= cells:
                    raise ValidationError("Wait zone is outside the battlefield")
            if wait_trigger.stop_thrust:
                if basic:
                    raise ValidationError("Basic stop thrust requires explicit GM adjudication")
                if (
                    wait_trigger.actor_id is None
                    or wait_trigger.reaction_target_id != wait_trigger.actor_id
                ):
                    raise ValidationError("Stop thrust requires one declared charging foe")
            commitment = commitment.model_copy(update={"wait": wait_trigger})
        if maneuver in ("evaluate", "aim", "feint"):
            target = next((p for p in encounter.participants if p.actor_id == target_id), None)
            if target is None or target.actor_id == actor_id:
                raise ValidationError("Maneuver requires another combatant target")
            reach = participant.reach + (
                participant.movement_allowance if maneuver == "evaluate" else 0
            )
            if (
                maneuver != "aim"
                and (
                    basic_distance(encounter, participant.actor_id, target.actor_id)
                    if basic
                    else engine.distance(participant.position, target.position)
                )
                > reach
            ):
                raise ValidationError("Maneuver target is outside melee reach")
            if maneuver == "evaluate":
                commitment = commitment.model_copy(
                    update={
                        "evaluate_target_id": target_id,
                        "evaluate_bonus": min(3, old.evaluate_bonus + 1)
                        if old.evaluate_target_id == target_id
                        else 1,
                    }
                )
            elif maneuver == "aim":
                if item_id not in participant.ready_item_ids:
                    raise ValidationError("Aim requires a ready ranged weapon")
                commitment = commitment.model_copy(
                    update={
                        "aim_item_id": item_id,
                        "aim_target_id": target_id,
                        "aim_seconds": min(3, old.aim_seconds + 1)
                        if (old.aim_item_id, old.aim_target_id) == (item_id, target_id)
                        else 1,
                    }
                )
        participant = participant.model_copy(update={"maneuver_state": commitment})
        step_maneuvers = {
            "attack",
            "aim",
            "evaluate",
            "feint",
            "ready",
            "concentrate",
            "all_out_defense",
        }
        if facing is not None and maneuver in step_maneuvers:
            participant = participant.model_copy(update={"facing": facing})
            facing = None
        if posture is not None and maneuver in step_maneuvers:
            if destination is not None or {posture, participant.posture} != {
                "standing",
                "kneeling",
            }:
                raise ValidationError("A posture step only switches standing and kneeling in place")
            participant = participant.model_copy(update={"posture": posture})
            posture = None
        # A single destination is one movement allowance, never a second action.
        if destination is not None and maneuver != "move" and not deferred_step:
            limit = max(1, (participant.movement_allowance + 9) // 10)
            if maneuver == "move_and_attack":
                limit = participant.movement_allowance
            elif maneuver == "all_out_attack" or (
                maneuver == "all_out_defense" and defense_option == "dodge"
            ):
                limit = participant.movement_allowance // 2
            elif maneuver not in (
                "attack",
                "aim",
                "evaluate",
                "feint",
                "ready",
                "concentrate",
                "all_out_defense",
            ):
                raise ValidationError("Maneuver does not permit a step")
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square movement requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != actor_id and isinstance(p.position, GridPoint)
            }
            assert battlefield is not None
            if not engine._reachable(
                battlefield,
                participant.position,
                destination,
                limit,
                occupied,
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
            post_attack_destination=destination if deferred_step else None,
            post_attack_square_facing=facing if deferred_step else None,
            post_attack_hex_path=hex_path if deferred_step else (),
            post_attack_facing=hex_facing if deferred_step else None,
            post_attack_posture=posture if deferred_step else None,
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
