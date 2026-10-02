"""Trusted immutable B253 channels and approved-build casting contexts."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellContext
from wayfarer.engine.simulation.magic.water_discovery import known_sources
from wayfarer.engine.simulation.magic.water_effects import WaterPlan, validate_operation
from wayfarer.engine.simulation.magic.water_state import latest, validate_body
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "water-channel:"


class WaterChannel(Record):
    id: Id
    actor_id: Id
    location_id: Id
    plan: WaterPlan
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    distance_yards: int = 0
    touching: bool = False
    visible: bool = True


class BoundWater(Record):
    context: SpellContext
    plan: WaterPlan


def channels(state: ResourceState) -> tuple[WaterChannel, ...]:
    return tuple(
        WaterChannel.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def validate_channel(
    state: PlayState, channel: WaterChannel, *, check_exclusions: bool = True
) -> None:
    actor = next((e for e in state.world.entities if e.id == channel.actor_id), None)
    if actor is None or actor.location_id != channel.location_id:
        raise ValidationError("Water caster left its authored location")
    if channel.distance_yards < 0:
        raise ValidationError("Water channel distance cannot be negative")
    if any(e.status == "active" and channel.actor_id in e.turn_order for e in state.encounters):
        raise ValidationError("Water combat placement requires its concrete spatial adapter")
    validate_operation(state.resources, channel.plan)
    bodies = latest(state.resources)
    if channel.plan.spell_id == "seek-water":
        known = {e.id for e in state.world.perspective(channel.actor_id).entities} | known_sources(
            state.resources, channel.actor_id
        )
        if check_exclusions and not set(channel.plan.excluded_source_ids) <= known:
            raise ValidationError("Only known sources may be excluded before seeking")
        for body in bodies.values():
            validate_body(state.world, body)
    else:
        for object_id in {
            channel.plan.target_id,
            channel.plan.source_id,
            *channel.plan.destroyed_ids,
        } - {None}:
            assert object_id is not None
            body = bodies[object_id]
            validate_body(state.world, body)
            if body.location_id != channel.location_id:
                raise ValidationError("Water object left the channel location")


def declare(state: PlayState, channel: WaterChannel, command_id: str) -> ResourceState:
    if any(c.id == channel.id for c in channels(state.resources)):
        raise ConflictError("Water channels cannot be replaced")
    validate_channel(state, channel)
    return state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=channel.actor_id,
                    kind=channel.model_dump_json(),
                ),
            )
        }
    )


def approved_context(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand
) -> BoundWater:
    synchronous(state, command.actor_id)
    channel = next((c for c in channels(state.resources) if c.id == command.channel_id), None)
    if channel is None or (channel.actor_id, channel.plan.spell_id) != (
        command.actor_id,
        command.spell_id,
    ):
        raise AuthorizationError("Water channel does not authorize this caster and spell")
    if (
        command.target_item_id is not None
        or command.hit_location is not None
        or command.position is not None
    ):
        raise ValidationError("Water uses its authored material channel")
    if command.kind not in ("cancel", "remember"):
        validate_channel(state, channel, check_exclusions=command.kind == "start")
    context = build_context(
        runtime,
        state,
        command,
        SpellEnvironment(
            target_id=channel.plan.target_id,
            mana=channel.mana,
            distance=0
            if channel.touching or command.spell_id == "seek-water"
            else channel.distance_yards,
            radius=channel.plan.radius,
            energy=1,
            unseen=not channel.visible and not channel.touching,
        ),
    )
    return BoundWater(
        context=context.model_copy(
            update={
                "execution_version": 2,
                "execute_effects": True,
                "location_id": channel.location_id,
                "area_targeting": command.spell_id == "destroy-water",
                "area": AreaSelection(center=channel.plan.area_center)
                if command.spell_id == "destroy-water"
                else None,
                "position": channel.plan.area_center
                if command.spell_id == "destroy-water"
                else None,
            }
        ),
        plan=channel.plan,
    )
