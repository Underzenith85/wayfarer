"""Legal-choice enumeration on a Basic-space battlefield."""

from __future__ import annotations

import hashlib
import json

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.vocabulary import Maneuver
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.errors import WayfarerError
from wayfarer.orchestration.combat import (
    COMBAT_ADAPTER,
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.preview import preview
from wayfarer.orchestration.tactical_view.records import TacticalChoice


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
        from wayfarer.engine.rules.types.object import residual_definition
        from wayfarer.engine.simulation.combat.objects.combat import effective_entry

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
