"""Persisted director-authored channels for the private lock-spell vocabulary."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.lock_channel_state import PREFIX
from wayfarer.engine.simulation.magic.lock_channel_state import LockChannel as LockChannel
from wayfarer.engine.simulation.magic.lock_channel_state import channels as channels
from wayfarer.engine.simulation.magic.lock_state import latest, validate_fixture
from wayfarer.engine.simulation.magic.spell_state import latest as spell_effects
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellContext
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError


def declare(
    runtime: RulesContext, state: PlayState, channel: LockChannel, command_id: str
) -> ResourceState:
    if any(c.id == channel.id for c in channels(state.resources)):
        raise ConflictError("A lock channel cannot be replaced")
    value = latest(state.resources).get(channel.target_id)
    if value is None or channel.location_id not in (
        value.fixture.passage or (value.fixture.location_id,)
    ):
        raise ValidationError("A lock channel requires its declared object location")
    if not any(a.actor_id == channel.actor_id and a.approval is not None for a in state.actors):
        raise ValidationError("A lock channel requires an approved caster")
    validate_fixture(state.world, state.resources, value.fixture)
    _validate_placement(runtime, state, channel)
    if any(
        c.target_id == channel.target_id
        and c.encounter_id == channel.encounter_id
        and c.encounter_id is not None
        and (c.position, c.geometry) != (channel.position, channel.geometry)
        for c in channels(state.resources)
    ):
        raise ValidationError("Lock channels disagree about the object's combat placement")
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
) -> SpellContext:
    synchronous(state, command.actor_id)
    channel = next((c for c in channels(state.resources) if c.id == command.channel_id), None)
    if channel is None or (channel.actor_id, channel.spell_id) != (
        command.actor_id,
        command.spell_id,
    ):
        raise AuthorizationError("Lock channel does not authorize this caster and spell")
    if command.kind in ("cancel", "maintain", "remember"):
        return _existing_context(runtime, state, command, channel)
    value = latest(state.resources).get(channel.target_id)
    if value is None:
        raise ValidationError("Lock channel object is missing")
    validate_fixture(state.world, state.resources, value.fixture)
    if (
        command.target_item_id is not None
        or command.hit_location is not None
        or command.position is not None
    ):
        raise ValidationError("Lock magic uses its authored object target")
    entities = {e.id: e for e in state.world.entities}
    if (
        command.kind not in ("cancel", "maintain", "remember")
        and entities[command.actor_id].location_id != channel.location_id
    ):
        raise ValidationError("Caster left the lock channel location")
    encounter = next(
        (e for e in state.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    distance = channel.distance_yards
    touching = channel.touching
    position = None
    if encounter is not None:
        if (
            channel.encounter_id != encounter.id
            or channel.position is None
            or channel.geometry != ("hex" if encounter.spatial_kind == "hex" else "square")
        ):
            raise ValidationError("Combat lock magic requires matching authoritative placement")
        position = channel.position
        caster = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        point = (
            Hex(q=position[0], r=position[1])
            if channel.geometry == "hex"
            else GridPoint(x=position[0], y=position[1])
        )
        if isinstance(point, Hex):
            runtime.require_hex(encounter).cell(point)
        else:
            board = (
                next(
                    (
                        b
                        for b in runtime.rules.combat.battlefields
                        if b.id == encounter.battlefield_id
                    ),
                    None,
                )
                if runtime.rules.combat
                else None
            )
            if not isinstance(board, Battlefield) or not (
                0 <= point.x < board.width and 0 <= point.y < board.height
            ):
                raise ValidationError("Lock placement is outside the battlefield")
        distance = CombatEngine.distance(caster.position, point)
        touching = touching and distance <= 1
    context = build_context(
        runtime,
        state,
        command,
        SpellEnvironment(
            target_id=channel.target_id,
            mana=channel.mana,
            distance=0 if touching else distance,
            radius=command.radius,
            energy=command.energy,
            unseen=not channel.visible and not touching,
        ),
    )
    return context.model_copy(
        update={
            "execution_version": 2,
            "execute_effects": True,
            "location_id": channel.location_id,
            "encounter_id": encounter.id if encounter else None,
            "position": position,
            "geometry": channel.geometry,
        }
    )


def _existing_context(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand, channel: LockChannel
) -> SpellContext:
    effect = spell_effects(state.resources).get(command.cast_id)
    if effect is None or (effect.actor_id, effect.spell_id, effect.target_id) != (
        command.actor_id,
        command.spell_id,
        channel.target_id,
    ):
        raise ConflictError("Lock lifecycle command does not identify the caster's existing effect")
    # B237-238: existing effects are cancelled or maintained without new target
    # placement, range or visibility checks, even if a new encounter has begun.
    context = build_context(
        runtime, state, command, SpellEnvironment(target_id=effect.target_id, mana=channel.mana)
    )
    return context.model_copy(
        update={
            "execution_version": 2,
            "execute_effects": True,
            "location_id": effect.location_id,
            "encounter_id": effect.encounter_id,
            "position": effect.position,
            "geometry": effect.geometry,
        }
    )


def _validate_placement(runtime: RulesContext, state: PlayState, channel: LockChannel) -> None:
    if channel.encounter_id is None:
        return
    encounter = next((e for e in state.encounters if e.id == channel.encounter_id), None)
    if encounter is None or encounter.status != "active":
        raise ValidationError("Lock channel requires a current encounter")
    if channel.geometry != ("hex" if encounter.spatial_kind == "hex" else "square"):
        raise ValidationError("Lock channel geometry disagrees with the encounter")
    board = (
        next(
            (b for b in runtime.rules.combat.battlefields if b.id == encounter.battlefield_id), None
        )
        if runtime.rules.combat
        else None
    )
    if board is None or board.location_id != channel.location_id:
        raise ValidationError("Lock channel placement is not at the encounter's world location")
    assert channel.position is not None
    x, y = channel.position
    if channel.geometry == "hex":
        runtime.require_hex(encounter).cell(Hex(q=x, r=y))
    elif not isinstance(board, Battlefield) or not (0 <= x < board.width and 0 <= y < board.height):
        raise ValidationError("Lock placement is outside the battlefield")
