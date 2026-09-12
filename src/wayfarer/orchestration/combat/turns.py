"""Validating, previewing and committing one combat turn."""

from __future__ import annotations

from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import exertion, injury_turn, movement
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn, TypedCombatCommand
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.entangle_transitions import escape_binding
from wayfarer.engine.simulation.combat.equipment_effects import stress, worn_stress
from wayfarer.engine.simulation.combat.firearm_transitions import service
from wayfarer.engine.simulation.combat.maneuver_transitions import observe
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.combat.melee.attack import prepare_attack, waive_off_hand_penalty
from wayfarer.engine.simulation.combat.melee.modes import mode, mode_reach, require_two_weapon_modes
from wayfarer.engine.simulation.combat.mounts import assign_crew
from wayfarer.engine.simulation.combat.objects.locations import (
    bind_ready_hand,
    validate_posture,
    validate_target,
)
from wayfarer.engine.simulation.combat.ranged.ammunition import reload_weapon, unload_weapon
from wayfarer.engine.simulation.combat.ranged.readiness import let_down
from wayfarer.engine.simulation.combat.ranged.situation import validate_command
from wayfarer.engine.simulation.combat.ranged.spraying import (
    prepare_spraying_fire,
    prepare_suppression_fire,
)
from wayfarer.engine.simulation.combat.thrown.flight import retrieve
from wayfarer.engine.simulation.combat.thrown.items import recover, undo_recovery
from wayfarer.engine.simulation.combat.unarmed.fighters import grapple_ready
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.context import CombatContext, CombatStep


def _validate_turn(
    state: PlayState, command: TakeCombatTurn, encounter: Encounter, context: CombatContext
) -> tuple[PlayState, Encounter]:
    play = context.play
    engine = context.engine
    resources = state.resources

    if command.maneuver != "do_nothing":
        require_not_dazed(resources, command.actor_id)

    validate_command(play.rules_context, state, encounter, command)
    resources = interrupt_concentration(resources, command.actor_id, command.id)
    state = state.model_copy(update={"resources": resources})
    if command.maneuver == "ready" and command.item_id:
        if command.recover_thrown_item:
            state = recover(play.rules_context, state, encounter, command)
        else:
            state = retrieve(state, encounter, command.actor_id, command.item_id)
        resources = state.resources
    if command.hit_location is not None and (
        command.maneuver not in ATTACK_MANEUVERS or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Hit location requires GURPS attack dispatch")
    if command.target_item_id and (
        command.maneuver not in ATTACK_MANEUVERS
        or command.hit_location
        or engine.rules.gurps_equipment is None
        or command.attack_option == "double"
    ):
        raise ValidationError("Object targeting requires a single GURPS attack")
    if command.ready_hand is not None and (
        command.maneuver != "ready" or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Hand selection requires GURPS Ready")
    if engine.rules.gurps_equipment is not None:
        validate_posture(state, command.actor_id, command.posture)
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    hp = next(p for p in resources.pools if p.id == f"hp:{actor.actor_id}")
    if (hp.injury.incapacitated if hp.injury else hp.current == 0) or actor.conditions:
        raise ValidationError("Incapacitated actor cannot act")
    if actor.available_at > resources.game_time and command.maneuver not in (
        "wait",
        "do_nothing",
    ):
        raise ValidationError("Actor is recovering from injury")
    if command.maneuver == "attack" and engine.rules.attacks:
        item = next((i for i in resources.items if i.id == command.item_id), None)
        if item is None or not any(
            p.definition_id == item.definition_id for p in engine.rules.attacks
        ):
            raise ValidationError("Unsupported combat weapon or attack mode")
    if command.mode_id is not None and (
        command.maneuver not in ATTACK_MANEUVERS | {"feint", "aim", "ready"}
        or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Weapon mode requires GURPS attack dispatch")
    if (
        command.maneuver in ATTACK_MANEUVERS | {"feint"}
        or command.maneuver == "aim"
        and command.transport_id is not None
    ) and engine.rules.gurps_equipment is not None:
        selected_mode = mode(
            play.rules_context,
            state,
            command.actor_id,
            command.item_id or "",
            command.mode_id,
        )
        if not command.suppression_zones:
            validate_target(
                play.rules_context,
                state,
                encounter,
                command.actor_id,
                command.target_id or "",
                selected_mode,
                command.hit_location,
            )

        if command.second_item_id is not None:
            second_mode = mode(
                play.rules_context,
                state,
                command.actor_id,
                command.second_item_id,
                command.second_mode_id,
            )
            require_two_weapon_modes(selected_mode, second_mode)

        encounter = engine._replace(
            encounter,
            next(p for p in encounter.participants if p.actor_id == command.actor_id).model_copy(
                update={"reach": mode_reach(selected_mode)}
            ),
        )
        if command.transport_id is not None:
            transport = next(
                (t for t in resources.transports if t.id == command.transport_id), None
            )
            participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
            mount = selected_mode.mount if isinstance(selected_mode, RangedMode) else None
            if (
                transport is None
                or transport.mechanics_version != 2
                or command.actor_id not in transport.occupants
                or mount is None
                or not mount.vehicle_mounted
            ):
                raise ValidationError("Vehicle fire requires its occupant and vehicle-mounted mode")
            if transport.last_turn == resources.game_time:
                raise ConflictError("Vehicle already acted this second")
            if (
                encounter.spatial_kind != "hex"
                or participant.position != Hex(q=transport.q, r=transport.r)
                or participant.hex_facing != transport.facing
            ):
                raise ValidationError("Vehicle and gunner require one synchronized encounter pose")
            crew = next(i for i in resources.items if i.id == command.item_id).mount_crew
            if not set(crew) <= set(transport.occupants):
                raise ValidationError("Vehicle-mounted weapon crew must occupy its vehicle")
    if (
        command.maneuver == "wait"
        and command.wait_trigger is not None
        and command.wait_trigger.stop_thrust
    ):
        if encounter.spatial_kind == "basic":
            raise ValidationError("Basic stop thrust requires explicit GM adjudication")

        assert command.wait_trigger.item_id is not None
        trigger_mode = mode(
            play.rules_context,
            state,
            command.actor_id,
            command.wait_trigger.item_id,
            command.wait_trigger.mode_id,
        )
        assert isinstance(trigger_mode, MeleeMode)
        waiter = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = engine._replace(
            encounter, waiter.model_copy(update={"reach": mode_reach(trigger_mode)})
        )
    return state, encounter


def _preview_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    hp: Pool,
    forced: bool,
) -> None:
    play = context.play
    engine = context.engine
    resources = state.resources
    if not forced and not (hp.injury and hp.injury.stunned):
        preview, _, preview_result = engine.take_turn(
            encounter,
            actor_id=command.actor_id,
            maneuver=command.maneuver,
            resources=resources,
            destination=command.destination,
            facing=command.facing,
            posture=command.posture,
            crouch=command.crouch,
            item_id=command.item_id,
            target_id=command.target_id,
            command_id=command.id,
            attack_option=command.attack_option,
            defense_option=command.defense_option,
            wait_trigger=command.wait_trigger,
            step_timing=command.step_timing,
            second_item_id=command.second_item_id,
            second_target_id=command.second_target_id,
            second_mode_id=command.second_mode_id,
            command_json=command.model_dump_json(),
            hex_path=command.hex_path,
            hex_facing=command.hex_facing,
            basic_move=command.basic_move,
            spatial_revision=(
                command.expected_revision + 1 if command.basic_move is not None else None
            ),
            suppression_fire=bool(command.suppression_zones),
        )
        if command.suppression_zones:
            prepare_suppression_fire(
                play.rules_context, state, preview, command, engine.hex_map(preview)
            )
        if preview.pending_defense is not None:
            preview = prepare_attack(
                play.rules_context,
                state,
                preview,
                preview.pending_defense.mode_id
                if preview.pending_defense.suppression_zone_id is not None
                else command.mode_id,
                hit_location=(
                    "random"
                    if preview.pending_defense.suppression_zone_id is not None
                    else command.hit_location
                ),
                target_item_id=command.target_item_id,
                shots=(
                    preview.pending_defense.shots
                    if preview.pending_defense.suppression_zone_id is not None
                    else command.shots
                ),
            )
            prepare_spraying_fire(play.rules_context, state, preview, command)
        if command.maneuver == "aim" and preview_result.code != "combat.wait_triggered":
            observe(play.rules_context, state, preview, command)
    return None


def _begin_turn(
    state: PlayState, command: TakeCombatTurn, encounter: Encounter, context: CombatContext
) -> tuple[PlayState, Encounter, TakeCombatTurn]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    resuming = context.resuming
    reaction = context.reaction
    resources = state.resources
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)

    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    encounter = engine._replace(
        encounter,
        participant.model_copy(
            update={"movement_allowance": movement(play.rules_context, state, command.actor_id)}
        ),
    )
    forced = participant.forced_do_nothing
    _preview_turn(state, command, encounter, context, hp, forced)
    if not resuming and not reaction:
        state = injury_turn(
            play.rules_context,
            state,
            command.actor_id,
            command.id,
            start=True,
            do_nothing=command.maneuver == "do_nothing"
            or forced
            or bool(hp.injury and hp.injury.stunned),
        )
    resources = state.resources
    started_hp = next(p for p in resources.pools if p.id == hp.id)
    assert started_hp.injury is not None
    allowed = not (started_hp.injury.incapacitated or started_hp.injury.stunned or forced)

    state, encounter = worn_stress(
        play.rules_context,
        state,
        encounter,
        command.actor_id,
        command.id,
    )
    resources = state.resources
    if allowed and command.maneuver != "do_nothing" and not resuming:
        state, allowed = exertion(play.rules_context, state, command.actor_id, command.id)
        resources = state.resources
    if (
        allowed
        and command.item_id
        and command.maneuver in ("attack", "all_out_attack", "move_and_attack", "aim", "feint")
    ):
        state, encounter = stress(
            play.rules_context,
            state,
            encounter,
            command.actor_id,
            command.id,
            (command.item_id,),
        )
        resources = state.resources
        allowed = any(
            i.id == command.item_id
            and i.ready
            and (
                i.condition is None
                or not i.condition.disabled
                or any(
                    old.id == i.id and old.condition and old.condition.disabled
                    for old in initial_state.resources.items
                )
            )
            for i in resources.items
        )
    encounter = engine._replace(
        encounter,
        next(p for p in encounter.participants if p.actor_id == command.actor_id).model_copy(
            update={
                "movement_allowance": movement(play.rules_context, state, command.actor_id)
                if allowed
                else 0
            }
        ),
    )
    if not allowed:
        if command.recover_thrown_item:
            resources = undo_recovery(initial_state.resources, resources, command.item_id)
            state = state.model_copy(update={"resources": resources})
        elif command.maneuver == "ready" and command.item_id:
            original = next(i for i in initial_state.resources.items if i.id == command.item_id)
            if original.ground is not None:
                resources = resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ground": original.ground})
                            if i.id == original.id
                            else i
                            for i in resources.items
                        )
                    }
                )
                state = state.model_copy(update={"resources": resources})
        command_for_turn = command.model_copy(
            update={
                "maneuver": "do_nothing",
                "shots": 1,
                "spray_targets": (),
                "suppression_zones": (),
                "reload_ammunition_id": None,
                "unload_ammunition": False,
                "fast_draw": False,
                "cocking_aid_id": None,
                "let_down_bow": False,
                "recover_thrown_item": False,
                "firearm_service": None,
                "firearm_service_skill": "weapon",
                "item_id": None,
                "mode_id": None,
                "target_id": None,
                "destination": None,
                "hex_path": (),
                "hex_facing": None,
                "facing": None,
                "posture": None,
                "crouch": None,
                "attack_option": None,
                "defense_option": None,
                "wait_trigger": None,
                "step_timing": "before",
                "second_item_id": None,
                "second_target_id": None,
                "second_mode_id": None,
                "braced": False,
                "hit_location": None,
                "ready_hand": None,
            }
        )
    else:
        command_for_turn = command
    return state, encounter, command_for_turn


def _prepare_attack_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    command_for_turn: TakeCombatTurn,
    resources: ResourceState,
    result: CombatResult,
) -> CombatStep | None:
    if context.engine.rules.gurps_equipment is None or result.code == "combat.wait_triggered":
        return None
    pending = encounter.pending_defense
    if pending is not None and pending.suppression_zone_id is not None:
        encounter = prepare_attack(
            context.play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            pending.mode_id,
            hit_location="random",
            shots=pending.shots,
        )
        assert encounter.pending_defense is not None
        result = result.model_copy(update={"available": encounter.pending_defense.allowed})
        return CombatStep(state, encounter, resources, result)
    if command_for_turn.maneuver not in ATTACK_MANEUVERS:
        return None
    if command_for_turn.suppression_zones:
        state, encounter = prepare_suppression_fire(
            context.play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command_for_turn,
            context.engine.hex_map(encounter),
        )
        state = injury_turn(
            context.play.rules_context,
            state,
            command.actor_id,
            command.id,
            start=False,
            do_nothing=False,
        )
        return CombatStep(state, encounter, state.resources, result)

    if command.laser_sight:
        assert encounter.pending_defense is not None
        encounter = encounter.model_copy(
            update={
                "pending_defense": encounter.pending_defense.model_copy(
                    update={"laser_sight": True}
                )
            }
        )
    encounter = prepare_attack(
        context.play.rules_context,
        state,
        encounter,
        command.mode_id,
        hit_location=command.hit_location,
        target_item_id=command.target_item_id,
        shots=command.shots,
    )
    encounter = prepare_spraying_fire(context.play.rules_context, state, encounter, command)
    assert encounter.pending_defense is not None
    if command.transport_id is not None:
        transport = next(t for t in resources.transports if t.id == command.transport_id)
        encounter = encounter.model_copy(
            update={
                "pending_defense": encounter.pending_defense.model_copy(
                    update={
                        "transport_id": transport.id,
                        "vehicle_attack_penalty": transport.attack_penalty,
                        "vehicle_aim_lost": transport.aim_lost,
                    }
                )
            }
        )
        resources = resources.model_copy(
            update={
                "transports": tuple(
                    t.model_copy(
                        update={
                            "attack_penalty": 0,
                            "aim_lost": False,
                            "last_turn": resources.game_time,
                        }
                    )
                    if t.id == transport.id
                    else t
                    for t in resources.transports
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    assert encounter.pending_defense is not None
    result = result.model_copy(update={"available": encounter.pending_defense.allowed})
    return CombatStep(state, encounter, resources, result)


def _after_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    command_for_turn: TakeCombatTurn,
    resources: ResourceState,
    result: CombatResult,
) -> CombatStep:

    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    reaction = context.reaction
    if command_for_turn.second_item_id is not None and result.code != "combat.wait_triggered":
        encounter = waive_off_hand_penalty(play.rules_context, state, encounter, command.actor_id)
    if (
        command_for_turn.maneuver == "ready"
        and engine.rules.gurps_equipment is not None
        and result.code != "combat.wait_triggered"
    ):
        if command_for_turn.reload_ammunition_id is not None:
            resources = reload_weapon(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                command_for_turn,
            )
        if command_for_turn.unload_ammunition:
            resources = unload_weapon(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                command_for_turn,
            )
        if command_for_turn.let_down_bow:
            selected = mode(
                play.rules_context,
                state,
                command.actor_id,
                command.item_id or "",
                command.mode_id,
            )
            assert isinstance(selected, RangedMode)
            resources = let_down(
                state.model_copy(update={"resources": resources}),
                command_for_turn,
                selected,
            )
        if command_for_turn.mount_crew:
            resources = assign_crew(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn,
            )
        if command_for_turn.escape_entanglement:
            encounter = escape_binding(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn.actor_id,
            )
        if command_for_turn.firearm_service is not None:
            resources = service(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn,
            )

        encounter = bind_ready_hand(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command.actor_id,
            command.item_id or "",
            command.ready_hand,
        )

        state, encounter = grapple_ready(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command_for_turn,
        )
        resources = state.resources
    if engine.rules.gurps_equipment is not None and result.code != "combat.wait_triggered":
        acted = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = engine._replace(
            encounter, acted.model_copy(update={"forced_do_nothing": False})
        )
    if (
        command_for_turn.maneuver in ("aim", "feint") or command_for_turn.attack_option == "feint"
    ) and result.code != "combat.wait_triggered":
        encounter = observe(play.rules_context, state, encounter, command_for_turn)
    prepared = _prepare_attack_turn(
        state, command, encounter, context, command_for_turn, resources, result
    )
    if prepared is not None:
        state, encounter, resources, result = (
            prepared.state,
            prepared.encounter,
            prepared.resources,
            prepared.result,
        )
    elif (
        engine.rules.gurps_equipment is not None
        and not reaction
        and result.code != "combat.wait_triggered"
    ):
        state = injury_turn(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            command.actor_id,
            command.id,
            start=False,
            do_nothing=command_for_turn.maneuver == "do_nothing",
        )
        resources = state.resources
    if (
        command_for_turn.maneuver == "aim"
        and command.transport_id is not None
        and result.code != "combat.wait_triggered"
    ):
        resources = resources.model_copy(
            update={
                "transports": tuple(
                    t.model_copy(update={"aim_lost": False, "last_turn": resources.game_time})
                    if t.id == command.transport_id
                    else t
                    for t in resources.transports
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    if command.recover_thrown_item and result.code == "combat.wait_triggered":
        resources = undo_recovery(initial_state.resources, resources, command.item_id)
        state = state.model_copy(update={"resources": resources})
    return CombatStep(state, encounter, resources, result)


def _take_turn(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    engine = context.engine
    assert isinstance(command, TakeCombatTurn)
    state, encounter = _validate_turn(state, command, encounter, context)
    if context.engine.rules.gurps_equipment is not None:
        state, encounter, command_for_turn = _begin_turn(state, command, encounter, context)
    else:
        command_for_turn = command
    resources = state.resources
    encounter, resources, result = engine.take_turn(
        encounter,
        actor_id=command.actor_id,
        maneuver=command_for_turn.maneuver,
        resources=resources,
        destination=command_for_turn.destination,
        facing=command_for_turn.facing,
        posture=command_for_turn.posture,
        crouch=command_for_turn.crouch,
        item_id=command_for_turn.item_id,
        target_id=command_for_turn.target_id,
        command_id=command.id,
        attack_option=command_for_turn.attack_option,
        defense_option=command_for_turn.defense_option,
        wait_trigger=command_for_turn.wait_trigger,
        step_timing=command_for_turn.step_timing,
        second_item_id=command_for_turn.second_item_id,
        second_target_id=command_for_turn.second_target_id,
        second_mode_id=command_for_turn.second_mode_id,
        command_json=command_for_turn.model_dump_json(),
        hex_path=command_for_turn.hex_path,
        hex_facing=command_for_turn.hex_facing,
        basic_move=command_for_turn.basic_move,
        spatial_revision=(
            command.expected_revision + 1 if command_for_turn.basic_move is not None else None
        ),
        suppression_fire=bool(command_for_turn.suppression_zones),
    )
    return _after_turn(state, command, encounter, context, command_for_turn, resources, result)
