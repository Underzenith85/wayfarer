"""Legal-choice enumeration on a hex battlefield."""

from __future__ import annotations

import hashlib
import json
from itertools import product

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.combat.vocabulary import Maneuver
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.hex_geometry import Hex, neighbor
from wayfarer.errors import WayfarerError
from wayfarer.orchestration.combat import (
    COMBAT_ADAPTER,
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.basic_choices import basic_choices
from wayfarer.orchestration.tactical_view.preview import preview
from wayfarer.orchestration.tactical_view.records import TacticalChoice


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
            from wayfarer.engine.simulation.combat.melee.modes import mode as weapon_mode
            from wayfarer.engine.simulation.combat.unarmed.fighters import free_hands

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
        from wayfarer.engine.rules.types.object import residual_definition
        from wayfarer.engine.simulation.combat.objects.combat import effective_entry

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


def adjacent(position: Hex) -> tuple[tuple[int, Hex], ...]:
    result: list[tuple[int, Hex]] = []
    for direction in range(6):
        try:
            result.append((direction, neighbor(position, direction)))
        except ValueError:
            continue
    return tuple(result)
