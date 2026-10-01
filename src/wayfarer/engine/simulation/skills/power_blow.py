"""B215 instantaneous Power Blow bound to one actual melee declaration."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from wayfarer.engine.character.traits.mastery import trained_by_master
from wayfarer.engine.rules.checks import Modifier, ModifierKind
from wayfarer.engine.rules.traits.mastery import covers
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.equipment.catalog import MeleeMode
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.skills.cinematic import (
    CinematicSkillCommand,
    CinematicSkillOutcome,
    apply_cinematic_skill,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.movement.physical import PhysicalRoute


class PowerBlowCommand(CinematicSkillCommand):
    skill_id: Literal["skill:power-blow"] = "skill:power-blow"
    encounter_id: Id
    attack_command_id: Id
    weapon_item_id: Id | None = None
    mode_id: Id | None = None
    multiplier: Literal[2, 3] = 2
    concentration_turns: Literal[0] = 0


class PowerBlowAid(Record):
    actor_id: Id
    build_revision: str
    encounter_id: Id
    attack_command_id: Id
    round: int = Field(ge=1)
    turn: int = Field(ge=0)
    weapon_item_id: Id | None = None
    mode_id: Id | None = None
    multiplier: Literal[1, 2, 3]


def _event_id(kind: str, command_id: str) -> str:
    return kind + hashlib.sha256(command_id.encode()).hexdigest()


def _matches_event(event_id: str, kind: str, command_id: str) -> bool:
    return event_id in (kind + command_id, _event_id(kind, command_id))


def activate_power_blow(
    runtime: RulesContext,
    state: PlayState,
    command: PowerBlowCommand,
    *,
    authorized_actor_id: str,
) -> tuple[PlayState, CinematicSkillOutcome]:
    """Preparation is instantaneous; never accept a claimed concentration history."""
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Power Blow actor lacks authority")
    compiled = build(runtime, state, command.actor_id)
    replay = any(r.command_id == command.id for r in state.resources.receipts)
    if replay:
        resources, result = apply_cinematic_skill(
            state.resources,
            state.world,
            compiled,
            command,
            authorized_actor_id=authorized_actor_id,
            rng=runtime.rng,
        )
        return state.model_copy(update={"resources": resources}), result
    encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
    if (
        encounter is None
        or encounter.current_actor_id != command.actor_id
        or encounter.pending_defense
        or encounter.pending_unarmed
    ):
        raise ValidationError("Power Blow requires the actor's current uncommitted melee turn")
    if not trained_by_master(compiled):
        if command.weapon_item_id is None or command.mode_id is None:
            raise ValidationError("Weapon Master Power Blow requires its mastered weapon")
        weapon = mode(runtime, state, command.actor_id, command.weapon_item_id, command.mode_id)
        if not isinstance(weapon, MeleeMode):
            raise ValidationError("Power Blow requires a melee weapon mode")
        item = next(i for i in state.resources.items if i.id == command.weapon_item_id)
        if not any(
            p.definition_id == "trait:advantage:weapon-master"
            and p.trait is not None
            and covers(p.trait, item.definition_id, weapon.hands, weapon.skill_id)
            for p in compiled.trait_purchases
        ):
            raise ValidationError("Power Blow weapon is outside the approved mastery scope")
    if command.multiplier == 3 and not any(
        v.target == "skill:power-blow" and v.value > 20 for v in compiled.sheet.values
    ):
        raise ValidationError("Triple ST requires Power Blow above skill 20")
    if any(
        _matches_event(e.id, "power-blow:", command.attack_command_id)
        for e in state.resources.events
    ):
        raise ConflictError("Attack already has a Power Blow attempt")
    modifiers: tuple[Modifier, ...] = ()
    if command.multiplier == 3:
        modifiers = (
            Modifier(
                -10,
                "Power Blow triple strength",
                "skill:power-blow",
                "B215",
                ModifierKind.SITUATIONAL,
            ),
        )
    resources, result = apply_cinematic_skill(
        state.resources,
        state.world,
        compiled,
        command,
        authorized_actor_id=authorized_actor_id,
        rng=runtime.rng,
        skill_modifiers=modifiers,
    )
    aid = PowerBlowAid(
        actor_id=command.actor_id,
        build_revision=compiled.revision,
        encounter_id=encounter.id,
        attack_command_id=command.attack_command_id,
        round=encounter.round,
        turn=encounter.turn_index,
        weapon_item_id=command.weapon_item_id if not trained_by_master(compiled) else None,
        mode_id=command.mode_id if not trained_by_master(compiled) else None,
        multiplier=command.multiplier if result.outcome in ("success", "critical-success") else 1,
    )
    resources = resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id("power-blow:", command.attack_command_id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=aid.model_dump_json(),
                ),
            )
        }
    )
    return state.model_copy(update={"resources": resources}), result


def power_blow_strength(
    resources: ResourceState,
    actor_id: str,
    build_revision: str,
    pending_id: str,
    encounter_id: str,
    round_number: int,
    turn: int,
    strength: int,
    weapon_item_id: str | None = None,
    mode_id: str | None = None,
) -> int:
    """A matching one-attack aid expires on intervening turns or clock advancement."""
    for event in reversed(resources.events):
        if (
            not event.id.startswith("power-blow:")
            or event.target_id != actor_id
            or event.at != resources.game_time
        ):
            continue
        aid = PowerBlowAid.model_validate_json(event.kind)
        digest = hashlib.sha256(aid.attack_command_id.encode()).hexdigest()
        armed_digest = hashlib.sha256(("combat:" + digest).encode()).hexdigest()
        if (
            aid.build_revision == build_revision
            and aid.encounter_id == encounter_id
            and aid.round == round_number
            and aid.turn == turn
            and (
                aid.weapon_item_id is None
                or (aid.weapon_item_id == weapon_item_id and aid.mode_id == mode_id)
            )
            and pending_id in ("defense:" + armed_digest, "unarmed:" + digest)
        ):
            return strength * aid.multiplier
    return strength


class PowerBlowLiftCommand(CinematicSkillCommand):
    skill_id: Literal["skill:power-blow"] = "skill:power-blow"
    route_id: Id
    lift_command_id: Id
    multiplier: Literal[2, 3] = 2
    concentration_turns: Literal[0] = 0


class PowerBlowLiftAid(Record):
    actor_id: Id
    build_revision: str
    route_id: Id
    lift_command_id: Id
    multiplier: Literal[1, 2, 3]


def activate_power_blow_lift(
    runtime: RulesContext,
    state: PlayState,
    command: PowerBlowLiftCommand,
    route: PhysicalRoute,
    *,
    authorized_actor_id: str,
) -> tuple[PlayState, CinematicSkillOutcome]:
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Power Blow actor lacks authority")
    compiled = build(runtime, state, command.actor_id)
    if any(r.command_id == command.id for r in state.resources.receipts):
        resources, result = apply_cinematic_skill(
            state.resources,
            state.world,
            compiled,
            command,
            authorized_actor_id=authorized_actor_id,
            rng=runtime.rng,
        )
        return state.model_copy(update={"resources": resources}), result
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    entity = next(e for e in state.world.entities if e.id == command.actor_id)
    if (
        route.id != command.route_id
        or route.kind != "lift"
        or route.scene_id != entity.location_id
        or actor.available_at > state.resources.game_time
        or any(e.status == "active" and command.actor_id in e.turn_order for e in state.encounters)
        or not route.pounds.is_finite()
        or route.pounds < 0
    ):
        raise ValidationError("Power Blow requires an available authored lifting route")
    if any(
        _matches_event(e.id, "power-lift:", command.lift_command_id) for e in state.resources.events
    ):
        raise ConflictError("Lift already has a Power Blow attempt")
    modifiers: tuple[Modifier, ...] = ()
    if command.multiplier == 3:
        if not any(v.target == "skill:power-blow" and v.value > 20 for v in compiled.sheet.values):
            raise ValidationError("Triple ST requires Power Blow above skill 20")
        modifiers = (
            Modifier(
                -10,
                "Power Blow triple strength",
                "skill:power-blow",
                "B215",
                ModifierKind.SITUATIONAL,
            ),
        )
    resources, result = apply_cinematic_skill(
        state.resources,
        state.world,
        compiled,
        command,
        authorized_actor_id=authorized_actor_id,
        rng=runtime.rng,
        skill_modifiers=modifiers,
    )
    aid = PowerBlowLiftAid(
        actor_id=command.actor_id,
        build_revision=compiled.revision,
        route_id=route.id,
        lift_command_id=command.lift_command_id,
        multiplier=command.multiplier if result.outcome in ("success", "critical-success") else 1,
    )
    resources = resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id("power-lift:", command.lift_command_id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=aid.model_dump_json(),
                ),
            )
        }
    )
    return state.model_copy(update={"resources": resources}), result


def power_blow_lift_strength(
    resources: ResourceState,
    actor_id: str,
    build_revision: str,
    route_id: str,
    command_id: str,
    strength: int,
) -> int:
    event = next(
        (e for e in resources.events if _matches_event(e.id, "power-lift:", command_id)), None
    )
    if event is None or event.at != resources.game_time or event.target_id != actor_id:
        return strength
    aid = PowerBlowLiftAid.model_validate_json(event.kind)
    return (
        strength * aid.multiplier
        if aid.build_revision == build_revision and aid.route_id == route_id
        else strength
    )
