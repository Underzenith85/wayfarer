"""Who can grapple whom: hands, grips, control and the strength behind it."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import Hand
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.objects.locations import unavailable_hand
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.unarmed_records import BASIC, wrestling_bonus
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn, TypedCombatCommand
    from wayfarer.engine.simulation.rules_context import RulesContext


def fighter(encounter: Encounter, actor_id: str) -> Combatant:
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Unarmed target is not in this encounter")
    return actor


def free_hands(state: PlayState, encounter: Encounter, actor_id: str) -> tuple[Hand, ...]:
    occupied = {h for _, h in fighter(encounter, actor_id).hand_bindings}
    occupied.update(h for g in encounter.grips if g.holder_id == actor_id for h in g.hands)
    # An arm grapple prevents use of that arm (B370).
    occupied.update(
        "left-hand" if g.location == "left-arm" else "right-hand"
        for g in encounter.grips
        if g.target_id == actor_id and g.location in ("left-arm", "right-arm")
    )
    unavailable = disabled(state.resources, actor_id)
    return tuple(
        h
        for h in ("left-hand", "right-hand")
        if h not in occupied and not unavailable_hand(unavailable, h)
    )


def settle_control(state: PlayState, encounter: Encounter) -> Encounter:
    def conscious(actor_id: str) -> bool:
        hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
        return (
            hp.injury is not None and not hp.injury.incapacitated and fatigue_ready(state, actor_id)
        )

    grips = tuple(
        g
        for g in encounter.grips
        if conscious(g.holder_id)
        and not any(unavailable_hand(disabled(state.resources, g.holder_id), h) for h in g.hands)
    )
    participants = tuple(
        p.model_copy(
            update={
                "arm_locked": any(g.target_id == p.actor_id and g.arm_lock for g in grips),
                "grappled": any(g.target_id == p.actor_id and g.location == "torso" for g in grips),
                "pinned": any(g.target_id == p.actor_id and g.pinned for g in grips),
            }
        )
        for p in encounter.participants
    )
    pairs = (
        encounter.close_pairs
        if isinstance(encounter.spatial, BasicSpatialContext)
        else tuple(
            pair
            for pair in encounter.close_pairs
            if fighter(encounter, pair[0]).position == fighter(encounter, pair[1]).position
        )
    )
    return encounter.model_copy(
        update={"grips": grips, "participants": participants, "close_pairs": pairs}
    )


def guard_control(encounter: Encounter, command: TypedCombatCommand, state: PlayState) -> None:
    from wayfarer.engine.simulation.combat.commands import (
        ChooseDefense,
        MigrateEncounterBasic,
        TakeCombatTurn,
        TakeUnarmedTurn,
    )

    if encounter.pending_unarmed is not None:
        if isinstance(command, MigrateEncounterBasic):
            return
        if (
            not isinstance(command, ChooseDefense)
            or command.actor_id != encounter.pending_unarmed.target_id
        ):
            raise ConflictError("Only the target may resolve the pending unarmed defense")
        return
    if isinstance(command, TakeUnarmedTurn):
        if fighter(encounter, command.actor_id).unarmed_balance_lost:
            raise ValidationError("Lost balance prevents even free actions until the next turn")
        if encounter.status != "active" or encounter.pending_defense or encounter.blocked_reason:
            raise ConflictError("Encounter cannot accept unarmed action now")
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            # Only the waiter's own declared reaction may act inside a paused turn.
            if interrupt.ready or interrupt.reacting or command.actor_id != interrupt.waiter_id:
                raise ConflictError("Resolve the interrupted Wait before another unarmed action")
        elif command.actor_id != encounter.current_actor_id:
            raise ConflictError("Unarmed action is out of turn")
    if isinstance(command, TakeCombatTurn):
        actor = fighter(encounter, command.actor_id)
        if actor.unarmed_balance_lost and command.maneuver != "do_nothing":
            raise ValidationError("Lost balance prevents actions until the next turn")
        if actor.pinned and command.maneuver != "do_nothing":
            raise ValidationError("Pinned actor must attempt a legal escape")
        engaged = any(command.actor_id in (g.holder_id, g.target_id) for g in encounter.grips)
        if engaged and (
            command.destination is not None or command.maneuver in ("move", "change_posture")
        ):
            raise ValidationError("Release or escape the grapple before moving")
        if engaged and command.maneuver == "ready":
            hands = (
                ("left-hand", "right-hand")
                if command.ready_hand == "both"
                else (command.ready_hand,)
            )
            if any(h not in free_hands(state, encounter, actor.actor_id) for h in hands):
                raise ValidationError("Ready while grappling requires explicit free usable hands")
            if command.reload_ammunition_id is not None or command.unload_ammunition:
                raise ValidationError("Reloading while grappling requires further integration")
        if engaged and command.maneuver in ("feint", "aim", "concentrate"):
            raise ValidationError("This maneuver while grappling requires further integration")
        if actor.grappled and command.posture is not None:
            raise ValidationError("A grapple prevents a posture step")


def skill_value(runtime: RulesContext, state: PlayState, actor_id: str, skill: str) -> int:
    compiled = build(runtime, state, actor_id)
    value = next((v for v in compiled.sheet.values if v.target == skill), None)
    if value is None:
        raise ValidationError("Selected unarmed skill has no compiled level")
    return int(value.value)


def grapple_ready(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: TakeCombatTurn,
) -> tuple[PlayState, Encounter]:
    """B371: drawing with a free hand requires DX; failure drops that item only."""
    from wayfarer.engine.simulation.resources import ResourceEvent

    if not any(g.target_id == command.actor_id for g in encounter.grips):
        return state, encounter
    actor = fighter(encounter, command.actor_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{command.actor_id}")
    assert hp.injury is not None
    score = skill_value(runtime, state, command.actor_id, "attribute:dx") - hp.injury.shock
    score -= 4 if actor.grappled else 0
    check = success_roll(
        BASIC, score, check_modifiers(state.resources, command.actor_id, "dx"), rng=runtime.rng
    )
    resources = state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id="grapple-ready:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=command.actor_id,
                    kind=TypeAdapter(CheckTrace).dump_json(check).decode(),
                ),
            ),
        }
    )
    if not check.outcome.succeeded:
        resources = resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"ready": False, "equipped": False})
                    if i.id == command.item_id
                    else i
                    for i in resources.items
                )
            }
        )
        actor = actor.model_copy(
            update={
                "ready_item_ids": tuple(i for i in actor.ready_item_ids if i != command.item_id),
                "hand_bindings": tuple(
                    (i, h) for i, h in actor.hand_bindings if i != command.item_id
                ),
            }
        )
        encounter = CombatEngine._replace(encounter, actor)
    return state.model_copy(update={"resources": resources}), encounter


def strength(
    runtime: RulesContext, state: PlayState, actor_id: str, *, trained: bool = True
) -> int:
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    wrestling = next(
        (int(v.value) for v in compiled.sheet.values if v.target == "skill:wrestling"), None
    )
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    return fatigue_value(fp, compiled.statistics.st) + (
        wrestling_bonus(compiled.statistics.dx, wrestling) if trained else 0
    )


def encumbrance_level(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    from wayfarer.engine.simulation.equipment.catalog import inventory_load

    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    level = inventory_load(
        catalog(runtime), runtime.resources, state.resources, actor_id, compiled.statistics
    ).level
    if level is None:
        raise ValidationError("Overloaded Judo/Karate requires a supported encumbrance level")
    return int(level)
