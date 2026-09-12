"""Allowlisted player views and no-dice legal-choice previews for tactical v1."""

from __future__ import annotations

import hashlib
import json
from itertools import product

from pydantic import Field

from wayfarer.errors import ValidationError, WayfarerError
from wayfarer.models import Record
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    COMBAT_ADAPTER,
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import BasicSpatialContext, Encounter, Maneuver
from wayfarer.simulation.gurps_equipment import MeleeMode, RangedMode
from wayfarer.simulation.hex_geometry import Cell, Hex, HexBattlefield, neighbor
from wayfarer.simulation.mechanics.gurps_melee import movement, prepare_attack
from wayfarer.simulation.mechanics.gurps_ranged import validate_command
from wayfarer.simulation.mechanics.tactical import prepare_defense
from wayfarer.simulation.mechanics.unarmed import guard_control, unarmed_defense, validate_action
from wayfarer.simulation.tactical import TacticalTrace, pose
from wayfarer.simulation.visibility import visible_actors as visible_actors


class TacticalActor(Record):
    id: str
    name: str
    position: Hex
    facing: int
    posture: str
    controlled: bool
    grappled: bool
    pinned: bool


class TacticalGrip(Record):
    id: str
    holder_id: str
    target_id: str
    location: str
    hands: tuple[str, ...]


class TacticalChoice(Record):
    label: str
    command: TakeCombatTurn | TakeUnarmedTurn | ChooseDefense | ResumeInterruptedTurn


class TacticalEncounter(Record):
    id: str
    status: str
    round: int
    current_actor_id: str | None
    coordinate_system: str = "hex-axial-v1"
    cells: tuple[Cell, ...]
    actors: tuple[TacticalActor, ...]
    grips: tuple[TacticalGrip, ...]
    choices: tuple[TacticalChoice, ...]
    notice: str | None = None
    traces: tuple[TacticalTrace, ...] = ()


class TacticalSnapshot(Record):
    version: str = "tactical-v1"
    campaign_id: str
    actor_id: str
    revision: int = Field(ge=0)
    encounters: tuple[TacticalEncounter, ...]


def legacy_encounter(
    state: PlayState,
    encounter: Encounter,
    member: CampaignMember,
    *,
    board: HexBattlefield | None = None,
) -> dict[str, object]:
    """Legacy consumers need turn/defense controls, never raw engine snapshots."""
    visible = frozenset(
        a
        for owner in member.actor_ids
        for a in visible_actors(state, encounter, owner, board=board)
    )
    order = tuple(a for a in encounter.turn_order if a in visible)
    pending = encounter.pending_defense
    return {
        "id": encounter.id,
        "status": encounter.status,
        "round": encounter.round,
        "turn_order": order,
        "turn_index": order.index(encounter.current_actor_id)
        if encounter.current_actor_id in order
        else -1,
        "pending_defense": {"defender_id": pending.defender_id, "allowed": pending.allowed}
        if pending and pending.defender_id in member.actor_ids
        else None,
    }


def preview(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn | TakeUnarmedTurn | ChooseDefense | ResumeInterruptedTurn,
) -> None:
    """Pure validation only: no execute, dice, mutation, or provisional receipts."""
    engine = play.engine.combat
    assert engine is not None
    from wayfarer.orchestration.recovery import guard
    from wayfarer.rules.hazard_types import require_hazards_settled
    from wayfarer.rules.recovery_types import require_settled

    guard(state, command.actor_id, command.kind)
    affected = {command.actor_id}
    if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and command.target_id is not None:
        affected.add(command.target_id)
    if isinstance(command, ChooseDefense):
        pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
        if pending:
            affected.update((pending.attacker_id, pending.defender_id))
        if unarmed:
            affected.update((unarmed.actor_id, unarmed.target_id))
    require_settled(state.resources.recovery_tasks, frozenset(affected), state.resources.game_time)
    affected.update(g.target_id for g in encounter.grips)
    require_hazards_settled(state.resources.hazards, frozenset(affected), state.resources.game_time)
    if isinstance(command, ResumeInterruptedTurn):
        interrupt = encounter.wait_interrupt
        if interrupt is None or not interrupt.ready or interrupt.actor_id != command.actor_id:
            raise ValidationError("No interrupted turn is ready")
        return
    guard_control(encounter, command, state)
    if isinstance(command, ChooseDefense):
        if command.catch_thrown:
            from wayfarer.simulation.mechanics.thrown_items import validate_catch

            validate_catch(play.rules_context, state, encounter, command)
        prepared = prepare_defense(play.rules_context, state, encounter, command)
        if prepared.pending_unarmed is not None:
            unarmed_defense(
                play.rules_context,
                state,
                prepared,
                command.actor_id,
                command.defense,
                command.item_id,
            )
        else:
            from wayfarer.simulation.mechanics.gurps_melee import validate_defense_choices

            if (
                prepared.pending_defense is None
                or prepared.pending_defense.defender_id != command.actor_id
            ):
                raise ValidationError("Defense is unavailable")
            validate_defense_choices(
                play.rules_context,
                state,
                prepared,
                command.defense,
                command.item_id,
                command.second_defense,
                command.second_item_id,
            )
        return
    if isinstance(command, TakeUnarmedTurn):
        validate_action(play.rules_context, state, encounter, command)
        return
    hp = next(p for p in state.resources.pools if p.id == f"hp:{command.actor_id}")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    if actor.conditions or (hp.injury and hp.injury.incapacitated):
        raise ValidationError("Actor is unavailable")
    if (hp.injury and hp.injury.stunned) and command.maneuver != "do_nothing":
        raise ValidationError("Actor must recover from stun")
    validate_command(play.rules_context, state, encounter, command)
    actor_state = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if actor_state.forced_do_nothing and command.maneuver != "do_nothing":
        raise ValidationError("Actor must do nothing")
    encounter = engine._replace(
        encounter,
        actor_state.model_copy(
            update={"movement_allowance": movement(play.rules_context, state, command.actor_id)}
        ),
    )
    if command.item_id and command.maneuver in (
        "attack",
        "all_out_attack",
        "move_and_attack",
        "feint",
    ):
        from wayfarer.simulation.mechanics.gurps_melee import mode

        selected = mode(
            play.rules_context, state, command.actor_id, command.item_id, command.mode_id
        )
        if isinstance(selected, MeleeMode):
            encounter = engine._replace(
                encounter,
                next(
                    p for p in encounter.participants if p.actor_id == command.actor_id
                ).model_copy(update={"reach": max(selected.reach)}),
            )
    result, _, _ = engine.take_turn(
        encounter,
        actor_id=command.actor_id,
        maneuver=command.maneuver,
        resources=state.resources,
        command_id=command.id,
        destination=command.destination,
        facing=command.facing,
        item_id=command.item_id,
        target_id=command.target_id,
        posture=command.posture,
        attack_option=command.attack_option,
        defense_option=command.defense_option,
        wait_trigger=command.wait_trigger,
        step_timing=command.step_timing,
        second_item_id=command.second_item_id,
        second_target_id=command.second_target_id,
        second_mode_id=command.second_mode_id,
        hex_path=command.hex_path,
        hex_facing=command.hex_facing,
        basic_move=command.basic_move,
        spatial_revision=command.expected_revision + 1 if command.basic_move is not None else None,
    )
    if result.pending_defense is not None:
        prepare_attack(
            play.rules_context,
            state,
            result,
            command.mode_id,
            hit_location=command.hit_location,
            target_item_id=command.target_item_id,
            shots=command.shots,
        )
    if command.maneuver == "aim":
        from wayfarer.simulation.mechanics.gurps_maneuvers import observe

        observe(play.rules_context, state, result, command)


def choices(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    visible: frozenset[str],
) -> tuple[TacticalChoice, ...]:
    if isinstance(encounter.spatial, BasicSpatialContext):
        return basic_choices(play, state, encounter, actor_id, visible)
    engine = play.engine.combat
    assert engine is not None
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    base = {
        "id": "preview",
        "actor_id": actor_id,
        "encounter_id": encounter.id,
        "expected_revision": state.revision,
    }
    candidates: list[tuple[str, dict[str, object]]] = []
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    interrupt = encounter.wait_interrupt
    if interrupt is not None and not pending and not unarmed:
        if interrupt.ready and interrupt.actor_id == actor_id:
            candidates.extend(
                (label, {"kind": "resume_interrupted_turn", "cancel": cancel})
                for label, cancel in (
                    ("Resume interrupted turn", False),
                    ("Cancel interrupted turn", True),
                )
            )
        elif not interrupt.ready and interrupt.waiter_id == actor_id:
            declaration = interrupt.declaration
            unarmed_reaction = declaration.unarmed
            waiter = next(p for p in encounter.participants if p.actor_id == actor_id)
            degraded = declaration.reaction == "all_out_attack" and waiter.maneuver_state.defended
            offer = declaration.reaction_target_id is None or (
                declaration.reaction_target_id in visible
            )
            if unarmed_reaction is not None:
                # An All-Out Attack reaction needs v2-only command options, which this
                # shared projection never emits; the waiter can still decline it here.
                offer = offer and (declaration.reaction == "attack" or degraded)
            if offer:
                candidates.append(
                    (
                        "Take declared Wait reaction",
                        {
                            "kind": "take_unarmed_turn",
                            "target_id": declaration.reaction_target_id,
                            **unarmed_reaction.model_dump(mode="json"),
                        }
                        if unarmed_reaction is not None
                        else {
                            "kind": "take_combat_turn",
                            "maneuver": declaration.reaction,
                            "item_id": declaration.item_id,
                            "target_id": declaration.reaction_target_id,
                            "mode_id": declaration.mode_id,
                            "attack_option": declaration.attack_option,
                        },
                    )
                )
            candidates.append(
                ("Decline Wait reaction", {"kind": "take_combat_turn", "maneuver": "do_nothing"})
            )
    elif pending or unarmed:
        defender_id = pending.defender_id if pending else unarmed.target_id if unarmed else None
        if defender_id != actor_id:
            return ()
        allowed = pending.allowed if pending else unarmed.allowed if unarmed else ()
        if pending:
            from wayfarer.simulation.mechanics.gurps_melee import mode as weapon_mode
            from wayfarer.simulation.mechanics.unarmed import free_hands

            incoming = (
                weapon_mode(
                    play.rules_context,
                    state,
                    pending.attacker_id,
                    pending.weapon_id,
                    pending.mode_id,
                )
                if pending.spell_cast_id is None
                else None
            )
            if isinstance(incoming, RangedMode) and incoming.catchable:
                for hand in free_hands(state, encounter, actor_id):
                    for catch in (False, True):
                        candidates.append(
                            (
                                f"Barehanded Parry with {hand}"
                                + ("; catch on critical success" if catch else ""),
                                {
                                    "kind": "choose_defense",
                                    "defense": "parry",
                                    "item_id": hand,
                                    "catch_thrown": catch,
                                },
                            )
                        )
        for defense in allowed:
            candidates.append(
                (f"{defense.title()} defense", {"kind": "choose_defense", "defense": defense})
            )
            if defense != "none":
                for direction, point in adjacent(pose(actor).position):
                    candidates.append(
                        (
                            f"{defense.title()} and retreat {direction}",
                            {
                                "kind": "choose_defense",
                                "defense": defense,
                                "retreat": point.model_dump(),
                            },
                        )
                    )
    elif (
        encounter.status == "active"
        and encounter.current_actor_id == actor_id
        and not encounter.blocked_reason
    ):
        for maneuver in ("do_nothing", "concentrate"):
            candidates.append(
                (
                    maneuver.replace("_", " ").title(),
                    {"kind": "take_combat_turn", "maneuver": maneuver},
                )
            )
        for posture in ("standing", "kneeling", "prone"):
            if posture != actor.posture:
                candidates.append(
                    (
                        f"Become {posture}",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": "change_posture",
                            "posture": posture,
                        },
                    )
                )
        for enhanced in ("dodge", "parry", "block", "double"):
            candidates.append(
                (
                    f"All-Out Defense: {enhanced}",
                    {
                        "kind": "take_combat_turn",
                        "maneuver": "all_out_defense",
                        "defense_option": enhanced,
                    },
                )
            )
        for direction, point in adjacent(pose(actor).position):
            candidates.append(
                (
                    f"Move to ({point.q}, {point.r})",
                    {
                        "kind": "take_combat_turn",
                        "maneuver": "move",
                        "hex_path": [point.model_dump()],
                    },
                )
            )
            if direction != actor.hex_facing:
                candidates.append(
                    (
                        f"Face direction {direction}",
                        {"kind": "take_combat_turn", "maneuver": "move", "hex_facing": direction},
                    )
                )
        rules = engine.rules.gurps_equipment
        assert rules is not None
        entries = {e.definition_id: e for e in rules.entries}
        from wayfarer.rules.object_types import residual_definition
        from wayfarer.simulation.mechanics.object_combat import effective_entry

        weapons = [
            (item, mode)
            for item in state.resources.items
            if item.owner_id == actor_id
            if item.firearm_failure is None or item.firearm_failure.kind != "destroyed"
            if item.condition is None
            or not item.condition.disabled
            or residual_definition(entries[item.definition_id].durability, item.condition)
            for mode in effective_entry(play.rules_context, item).modes
        ]
        for item, mode in weapons:
            item_name = play.engine.reviewer.compiler.definitions[item.definition_id].name
            if item.id not in actor.ready_item_ids:
                candidates.append(
                    (
                        f"Ready {item_name}",
                        {"kind": "take_combat_turn", "maneuver": "ready", "item_id": item.id},
                    )
                )
            if isinstance(mode, RangedMode) and mode.ammunition_id:
                for ammo in state.resources.items:
                    if ammo.owner_id == actor_id and ammo.definition_id == mode.ammunition_id:
                        candidates.append(
                            (
                                f"Reload {item_name}",
                                {
                                    "kind": "take_combat_turn",
                                    "maneuver": "ready",
                                    "item_id": item.id,
                                    "mode_id": mode.id,
                                    "reload_ammunition_id": ammo.id,
                                },
                            )
                        )
        for target in encounter.participants:
            if target.actor_id == actor_id or target.actor_id not in visible:
                continue
            target_id = target.actor_id
            target_name = next(e.name for e in state.world.entities if e.id == target_id)
            candidates.append(
                (
                    f"Evaluate {target_name}",
                    {"kind": "take_combat_turn", "maneuver": "evaluate", "target_id": target_id},
                )
            )
            for item, mode in weapons:
                attack_fields: dict[str, object] = {
                    "kind": "take_combat_turn",
                    "target_id": target_id,
                    "item_id": item.id,
                    "mode_id": mode.id,
                }
                for option in (
                    ("determined",)
                    if isinstance(mode, RangedMode)
                    else ("determined", "strong", "double", "feint")
                ):
                    candidates.append(
                        (
                            f"All-Out Attack ({option}) {target_name} — {mode.id}",
                            {
                                **attack_fields,
                                "maneuver": "all_out_attack",
                                "attack_option": option,
                            },
                        )
                    )
                for _direction, point in adjacent(pose(actor).position):
                    candidates.append(
                        (
                            f"Move and Attack {target_name} via ({point.q}, {point.r}) — {mode.id}",
                            {
                                **attack_fields,
                                "maneuver": "move_and_attack",
                                "hex_path": [point.model_dump()],
                            },
                        )
                    )
                for target_item in state.resources.items:
                    if (
                        target_item.owner_id == target_id
                        and (target_item.equipped or target_item.ground)
                        and target_item.condition
                    ):
                        candidates.append(
                            (
                                f"Strike {target_name}'s {target_item.definition_id}",
                                {
                                    **attack_fields,
                                    "maneuver": "attack",
                                    "target_item_id": target_item.id,
                                },
                            )
                        )
                if isinstance(mode, MeleeMode):
                    candidates.append(
                        (
                            f"Wait for {target_name} to attack — {mode.id}",
                            {
                                "kind": "take_combat_turn",
                                "maneuver": "wait",
                                "wait_trigger": {
                                    "actor_id": target_id,
                                    "action": "attack",
                                    "reaction": "attack",
                                    "reaction_target_id": target_id,
                                    "item_id": item.id,
                                    "mode_id": mode.id,
                                },
                            },
                        )
                    )
                    if mode.damage.basis == "thrust":
                        candidates.append(
                            (
                                f"Stop thrust against {target_name} — {mode.id}",
                                {
                                    "kind": "take_combat_turn",
                                    "maneuver": "wait",
                                    "wait_trigger": {
                                        "actor_id": target_id,
                                        "action": "attack",
                                        "reaction": "attack",
                                        "reaction_target_id": target_id,
                                        "item_id": item.id,
                                        "mode_id": mode.id,
                                        "stop_thrust": True,
                                    },
                                },
                            )
                        )
                    for _direction, point in adjacent(pose(actor).position):
                        candidates.append(
                            (
                                f"Attack then step to ({point.q}, {point.r}) — {mode.id}",
                                {
                                    **attack_fields,
                                    "maneuver": "attack",
                                    "step_timing": "after",
                                    "hex_path": [point.model_dump()],
                                },
                            )
                        )
                        candidates.append(
                            (
                                f"Wait for {target_name} to enter ({point.q}, {point.r})",
                                {
                                    "kind": "take_combat_turn",
                                    "maneuver": "wait",
                                    "wait_trigger": {
                                        "actor_id": target_id,
                                        "action": "move",
                                        "zone": ((point.q, point.r),),
                                        "reaction": "attack",
                                        "reaction_target_id": target_id,
                                        "item_id": item.id,
                                        "mode_id": mode.id,
                                    },
                                },
                            )
                        )
                maneuvers: tuple[Maneuver, ...] = (
                    ("attack", "aim") if isinstance(mode, RangedMode) else ("attack", "feint")
                )
                for maneuver in maneuvers:
                    candidates.append(
                        (
                            f"{maneuver.title()} {target_name} — {mode.id}",
                            {
                                "kind": "take_combat_turn",
                                "maneuver": maneuver,
                                "target_id": target_id,
                                "item_id": item.id,
                                "mode_id": mode.id,
                            },
                        )
                    )
                    if maneuver == "aim" and isinstance(mode, RangedMode) and mode.brace_kind:
                        candidates.append(
                            (
                                f"Aim braced at {target_name} — {mode.id}",
                                {
                                    **attack_fields,
                                    "maneuver": "aim",
                                    "braced": True,
                                },
                            )
                        )
            melee_weapons = [
                (item, mode)
                for item, mode in weapons
                if isinstance(mode, MeleeMode)
                and mode.hands == 1
                and item.id in actor.ready_item_ids
            ]
            for (first_item, first_mode), (second_item, second_mode) in product(
                melee_weapons, melee_weapons
            ):
                if first_item.id >= second_item.id:
                    continue
                candidates.append(
                    (
                        f"All-Out Attack (Double) {target_name} — two weapons",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": "all_out_attack",
                            "attack_option": "double",
                            "target_id": target_id,
                            "item_id": first_item.id,
                            "mode_id": first_mode.id,
                            "second_target_id": target_id,
                            "second_item_id": second_item.id,
                            "second_mode_id": second_mode.id,
                        },
                    )
                )
            for action, hands in product(
                ("punch", "kick", "grapple"),
                (("left-hand",), ("right-hand",), ("left-hand", "right-hand")),
            ):
                if action == "kick" and hands != ("left-hand",):
                    continue
                candidates.append(
                    (
                        f"{action.title()} {target_name} ({', '.join(hands) if action != 'kick' else 'right foot'})",
                        {
                            "kind": "take_unarmed_turn",
                            "target_id": target_id,
                            "action": action,
                            "hands": () if action == "kick" else hands,
                            "enter_close_combat": action != "kick"
                            and actor.position != target.position,
                        },
                    )
                )
            opportunity = actor.unarmed_lock_opportunity
            if opportunity is not None and opportunity[0] == target_id:
                for arm in ("left-arm", "right-arm"):
                    candidates.append(
                        (
                            f"Arm lock after parry — {target_name}, {arm.replace('-', ' ')}",
                            {
                                "kind": "take_unarmed_turn",
                                "target_id": target_id,
                                "action": "arm_lock",
                                "hands": ("left-hand", "right-hand"),
                                "location": arm,
                                "skill": opportunity[1],
                                "enter_close_combat": actor.position != target.position,
                            },
                        )
                    )
            for grip in encounter.grips:
                for action in (
                    "break_free",
                    "release",
                    "takedown",
                    "pin",
                    "strangle",
                    "arm_lock",
                    "lock_damage",
                ):
                    if actor_id not in (grip.holder_id, grip.target_id) or target_id not in (
                        grip.holder_id,
                        grip.target_id,
                    ):
                        continue
                    candidates.append(
                        (
                            f"{action.replace('_', ' ').title()} — {target_name}",
                            {
                                "kind": "take_unarmed_turn",
                                "action": action,
                                "target_id": target_id,
                                "grip_id": grip.id,
                                "hands": grip.hands if action == "arm_lock" else (),
                                "location": "left-arm" if action == "arm_lock" else "torso",
                                "skill": grip.skill if action == "arm_lock" else "attribute:dx",
                            },
                        )
                    )
    result: list[TacticalChoice] = []
    seen: set[str] = set()
    for label, fields in candidates:
        try:
            command = COMBAT_ADAPTER.validate_json(json.dumps({**base, **fields}))
            if not isinstance(
                command, (TakeCombatTurn, TakeUnarmedTurn, ChooseDefense, ResumeInterruptedTurn)
            ):
                continue
            preview(play, state, encounter, command)
            payload = command.model_dump_json()
            if payload in seen:
                continue
            seen.add(payload)
            identifier = (
                "tactical:" + hashlib.sha256((state.campaign_id + payload).encode()).hexdigest()
            )
            result.append(
                TacticalChoice(label=label, command=command.model_copy(update={"id": identifier}))
            )
        except WayfarerError, ValueError:
            continue
    return tuple(result)


def basic_choices(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    visible: frozenset[str],
) -> tuple[TacticalChoice, ...]:
    """Project only no-dice Basic commands that pass the authoritative preview."""
    engine = play.engine.combat
    assert engine is not None
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    base = {
        "id": "preview",
        "actor_id": actor_id,
        "encounter_id": encounter.id,
        "expected_revision": state.revision,
    }
    candidates: list[tuple[str, dict[str, object]]] = []
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    interrupt = encounter.wait_interrupt
    if interrupt is not None and not pending and not unarmed:
        if interrupt.ready and interrupt.actor_id == actor_id:
            candidates.extend(
                (label, {"kind": "resume_interrupted_turn", "cancel": cancel})
                for label, cancel in (
                    ("Resume interrupted turn", False),
                    ("Cancel interrupted turn", True),
                )
            )
        elif not interrupt.ready and interrupt.waiter_id == actor_id:
            declaration = interrupt.declaration
            if declaration.reaction_target_id is None or declaration.reaction_target_id in visible:
                candidates.append(
                    (
                        "Take declared Wait reaction",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": declaration.reaction,
                            "item_id": declaration.item_id,
                            "target_id": declaration.reaction_target_id,
                            "mode_id": declaration.mode_id,
                            "attack_option": declaration.attack_option,
                        },
                    )
                )
            candidates.append(
                ("Decline Wait reaction", {"kind": "take_combat_turn", "maneuver": "do_nothing"})
            )
    elif pending or unarmed:
        defender_id = pending.defender_id if pending else unarmed.target_id if unarmed else None
        if defender_id != actor_id:
            return ()
        allowed = pending.allowed if pending else unarmed.allowed if unarmed else ()
        for defense in allowed:
            candidates.append(
                (f"{defense.title()} defense", {"kind": "choose_defense", "defense": defense})
            )
            if defense != "none":
                candidates.append(
                    (
                        f"{defense.title()} and retreat",
                        {
                            "kind": "choose_defense",
                            "defense": defense,
                            "basic_retreat": True,
                        },
                    )
                )
    elif (
        encounter.status == "active"
        and encounter.current_actor_id == actor_id
        and not encounter.blocked_reason
    ):
        for maneuver in ("do_nothing", "concentrate"):
            candidates.append(
                (
                    maneuver.replace("_", " ").title(),
                    {"kind": "take_combat_turn", "maneuver": maneuver},
                )
            )
        for posture in ("standing", "kneeling", "prone"):
            if posture != actor.posture:
                candidates.append(
                    (
                        f"Become {posture}",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": "change_posture",
                            "posture": posture,
                        },
                    )
                )
        for enhanced in ("dodge", "parry", "block", "double"):
            candidates.append(
                (
                    f"All-Out Defense: {enhanced}",
                    {
                        "kind": "take_combat_turn",
                        "maneuver": "all_out_defense",
                        "defense_option": enhanced,
                    },
                )
            )

        rules = engine.rules.gurps_equipment
        from wayfarer.rules.object_types import residual_definition
        from wayfarer.simulation.mechanics.object_combat import effective_entry

        entries = {entry.definition_id: entry for entry in rules.entries} if rules else {}
        weapons = (
            [
                (item, mode)
                for item in state.resources.items
                if item.owner_id == actor_id
                if item.firearm_failure is None or item.firearm_failure.kind != "destroyed"
                if item.condition is None
                or not item.condition.disabled
                or residual_definition(entries[item.definition_id].durability, item.condition)
                for mode in effective_entry(play.rules_context, item).modes
            ]
            if rules
            else []
        )
        for item, _mode in weapons:
            item_name = play.engine.reviewer.compiler.definitions[item.definition_id].name
            if item.id not in actor.ready_item_ids:
                candidates.append(
                    (
                        f"Ready {item_name}",
                        {"kind": "take_combat_turn", "maneuver": "ready", "item_id": item.id},
                    )
                )
        names = {entity.id: entity.name for entity in state.world.entities}
        for target in encounter.participants:
            if target.actor_id == actor_id or target.actor_id not in visible:
                continue
            target_id, target_name = target.actor_id, names[target.actor_id]
            for direction in ("approach", "withdraw"):
                candidates.append(
                    (
                        f"{direction.title()} {target_name}",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": "move",
                            "basic_move": {
                                "reference_actor_id": target_id,
                                "direction": direction,
                            },
                        },
                    )
                )
            candidates.append(
                (
                    f"Evaluate {target_name}",
                    {"kind": "take_combat_turn", "maneuver": "evaluate", "target_id": target_id},
                )
            )
            for item, mode in weapons:
                attack_maneuver: Maneuver = "aim" if isinstance(mode, RangedMode) else "attack"
                candidates.append(
                    (
                        f"{attack_maneuver.title()} {target_name} — {mode.id}",
                        {
                            "kind": "take_combat_turn",
                            "maneuver": attack_maneuver,
                            "target_id": target_id,
                            "item_id": item.id,
                            "mode_id": mode.id,
                        },
                    )
                )
                if isinstance(mode, MeleeMode):
                    candidates.append(
                        (
                            f"Wait for {target_name} to attack — {mode.id}",
                            {
                                "kind": "take_combat_turn",
                                "maneuver": "wait",
                                "wait_trigger": {
                                    "actor_id": target_id,
                                    "action": "attack",
                                    "reaction": "attack",
                                    "reaction_target_id": target_id,
                                    "item_id": item.id,
                                    "mode_id": mode.id,
                                },
                            },
                        )
                    )

    result: list[TacticalChoice] = []
    seen: set[str] = set()
    for label, fields in candidates:
        try:
            command = COMBAT_ADAPTER.validate_json(json.dumps({**base, **fields}))
            if not isinstance(
                command, (TakeCombatTurn, TakeUnarmedTurn, ChooseDefense, ResumeInterruptedTurn)
            ):
                continue
            preview(play, state, encounter, command)
            payload = command.model_dump_json()
            if payload in seen:
                continue
            seen.add(payload)
            identifier = (
                "tactical:" + hashlib.sha256((state.campaign_id + payload).encode()).hexdigest()
            )
            result.append(
                TacticalChoice(label=label, command=command.model_copy(update={"id": identifier}))
            )
        except WayfarerError, ValueError:
            continue
    return tuple(result)


async def snapshot(
    access: CampaignAccess, cid: str, principal: str, actor_id: str
) -> TacticalSnapshot:
    access = await access.runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    member = access._member(state, principal)
    access._control(member, actor_id)
    return project(access.play, state, member, actor_id)


def adjacent(position: Hex) -> tuple[tuple[int, Hex], ...]:
    result: list[tuple[int, Hex]] = []
    for direction in range(6):
        try:
            result.append((direction, neighbor(position, direction)))
        except ValueError:
            continue
    return tuple(result)


def project(
    play: PlayService,
    state: PlayState,
    member: CampaignMember,
    actor_id: str,
    *,
    include_object_choices: bool = False,
) -> TacticalSnapshot:
    views: list[TacticalEncounter] = []
    entities = {e.id: e for e in state.world.entities}
    for encounter in state.encounters:
        board = play.rules_context.hex_map(encounter)
        if board is None or actor_id not in encounter.turn_order:
            continue
        visible = visible_actors(
            state, encounter, actor_id, board=play.rules_context.hex_map(encounter)
        )
        own = next(p for p in encounter.participants if p.actor_id == actor_id)
        from wayfarer.simulation.hex_geometry import SightPoint, line_of_sight

        cells = tuple(
            cell
            for cell in board.cells
            if line_of_sight(
                board,
                SightPoint(position=pose(own).position, height=1 if own.posture != "prone" else 0),
                SightPoint(position=cell.position, height=1),
            )
        )
        views.append(
            TacticalEncounter(
                id=encounter.id,
                status=encounter.status,
                round=encounter.round,
                current_actor_id=encounter.current_actor_id
                if encounter.current_actor_id in visible
                else None,
                cells=cells,
                actors=tuple(
                    TacticalActor(
                        id=p.actor_id,
                        name=entities[p.actor_id].name,
                        position=pose(p).position,
                        facing=pose(p).facing,
                        posture=p.posture,
                        controlled=p.actor_id == actor_id,
                        grappled=p.grappled,
                        pinned=p.pinned,
                    )
                    for p in encounter.participants
                    if p.actor_id in visible
                ),
                grips=tuple(
                    TacticalGrip(
                        id=g.id,
                        holder_id=g.holder_id,
                        target_id=g.target_id,
                        location=g.location,
                        hands=g.hands,
                    )
                    for g in encounter.grips
                    if g.holder_id in visible and g.target_id in visible
                ),
                choices=tuple(
                    c
                    for c in choices(play, state, encounter, actor_id, visible)
                    if include_object_choices
                    or not (
                        isinstance(c.command, TakeCombatTurn)
                        and c.command.target_item_id
                        or isinstance(c.command, ChooseDefense)
                        and c.command.item_id in ("left-hand", "right-hand")
                        and encounter.pending_defense is not None
                    )
                )
                if state.lifecycle == "active"
                else (),
                notice="This encounter requires resolution of an unsupported rule."
                if encounter.blocked_reason
                else None,
                traces=tuple(t for t in encounter.tactical_traces if t.actor_id == actor_id),
            )
        )
    return TacticalSnapshot(
        campaign_id=state.campaign_id,
        actor_id=actor_id,
        revision=state.revision,
        encounters=tuple(views),
    )
