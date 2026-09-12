"""Psionic Talent adapter and event-backed Antipsi interference."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.psi_powers import PsiLoadout, PsiPowers, psi_powers
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.traits.psi_powers import BINDING_BY_ID
from wayfarer.engine.simulation.abilities import AbilityContext
from wayfarer.engine.simulation.ability_types import AbilitySpec
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState, Scheduled
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "psi-interference:"


class PsiInterference(Record):
    id: str
    ability_id: Literal["advantage:neutralize", "advantage:psi-static"]
    actor_id: str
    target_actor_id: str | None = None
    location_id: str
    blocked_power_id: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1)


class PsiInterferenceCommand(Command):
    interference_id: str
    action: Literal["activate", "deactivate"]


class PsiInterferenceOutcome(Record):
    action: Literal["activated", "deactivated"]
    interference_id: str
    effect_id: str
    expires_at: int | None = Field(default=None, ge=0)


class PsiInterferenceEvent(Record):
    command_id: str
    outcome: PsiInterferenceOutcome


def _id(value: str, purpose: str) -> str:
    return PREFIX + purpose + ":" + hashlib.sha256(value.encode()).hexdigest()


def _effect_id(interference_id: str) -> str:
    return _id(interference_id, "effect")


def history(resources: ResourceState) -> tuple[PsiInterferenceEvent, ...]:
    return tuple(
        PsiInterferenceEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX + "event:")
    )


def apply_interference(
    resources: ResourceState,
    world: World,
    command: PsiInterferenceCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    loadout: PsiLoadout,
    authored: tuple[PsiInterference, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, PsiInterferenceOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Antipsi interference requires actor authority")
    previous = next((event for event in history(resources) if event.command_id == command.id), None)
    if previous is not None:
        if previous.outcome.interference_id == command.interference_id:
            return resources, previous.outcome
        raise ConflictError("Antipsi command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Antipsi revision changed")
    interference = next((value for value in authored if value.id == command.interference_id), None)
    if interference is None or interference.actor_id != command.actor_id:
        raise ValidationError("Authored Antipsi interference is unavailable")
    entities = {entity.id: entity for entity in world.entities}
    actor = entities.get(command.actor_id)
    location = entities.get(interference.location_id)
    target = (
        None if interference.target_actor_id is None else entities.get(interference.target_actor_id)
    )
    if (
        actor is None
        or actor.location_id != interference.location_id
        or location is None
        or location.kind is not EntityKind.LOCATION
        or (
            interference.target_actor_id is not None
            and (
                target is None
                or target.kind is not EntityKind.ACTOR
                or target.location_id != interference.location_id
            )
        )
    ):
        raise ValidationError("Antipsi context changed")
    projected = psi_powers(build, definitions, loadout)
    if interference.ability_id not in projected.abilities("power:antipsi"):
        raise ValidationError("Antipsi ability is not in the approved loadout")
    if interference.blocked_power_id is not None and interference.blocked_power_id not in {
        value for value in BINDING_BY_ID if value != "power:antipsi"
    }:
        raise ValidationError("Unknown psionic power suppression target")
    if interference.ability_id == "advantage:neutralize" and target is None:
        raise ValidationError("Neutralize requires an authored target")
    if interference.ability_id == "advantage:psi-static" and target is not None:
        raise ValidationError("Psi Static is an area around its owner")
    effect_id = _effect_id(interference.id)
    active = effect_id in resources.active_effect_ids
    if command.action == "activate" and active:
        raise ConflictError("Antipsi interference is already active")
    if command.action == "deactivate" and not active:
        raise ConflictError("Antipsi interference is not active")
    effects = set(resources.active_effect_ids)
    scheduled = tuple(value for value in resources.scheduled if value.target_id != effect_id)
    expires_at = None
    if command.action == "activate":
        effects.add(effect_id)
        if interference.duration_seconds is not None:
            expires_at = resources.game_time + interference.duration_seconds
            scheduled += (
                Scheduled(
                    id=_id(interference.id, "expiry"),
                    due=expires_at,
                    kind="expire",
                    target_id=effect_id,
                ),
            )
        action: Literal["activated", "deactivated"] = "activated"
    else:
        effects.remove(effect_id)
        action = "deactivated"
    outcome = PsiInterferenceOutcome(
        action=action,
        interference_id=interference.id,
        effect_id=effect_id,
        expires_at=expires_at,
    )
    event = PsiInterferenceEvent(command_id=command.id, outcome=outcome)
    state = resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "active_effect_ids": tuple(sorted(effects)),
            "scheduled": scheduled,
            "events": resources.events
            + (
                ResourceEvent(
                    id=_id(command.id, "event"),
                    at=resources.game_time,
                    kind=event.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )
    return state, outcome


def power_is_blocked(
    resources: ResourceState,
    world: World,
    authored: tuple[PsiInterference, ...],
    *,
    actor_id: str,
    power_id: str,
) -> bool:
    actor = next((entity for entity in world.entities if entity.id == actor_id), None)
    if actor is None or actor.kind is not EntityKind.ACTOR:
        raise ValidationError("Psionic actor is unavailable")
    return any(
        _effect_id(value.id) in resources.active_effect_ids
        and value.actor_id != actor_id
        and value.location_id == actor.location_id
        and (value.target_actor_id is None or value.target_actor_id == actor_id)
        and (value.blocked_power_id is None or value.blocked_power_id == power_id)
        for value in authored
    )


def apply_ability_context(
    context: AbilityContext,
    spec: AbilitySpec,
    powers: PsiPowers,
    *,
    power_id: str,
    target_resistance: int = 0,
    living_sentient_target: bool = True,
    blocked: bool = False,
) -> AbilityContext:
    """Bind Talent, resistance, and suppression into the existing ability service."""
    if spec.definition_id not in powers.abilities(power_id):
        raise ValidationError("Ability is not in the approved psionic loadout")
    if blocked:
        raise ValidationError("Psionic power is suppressed")
    if power_id == "power:telepathy" and not living_sentient_target:
        raise ValidationError("Telepathy requires a living sentient subject")
    bonus = powers.activation_bonus(power_id, spec.definition_id)
    return context.__class__(
        profile_id=context.profile_id,
        level=context.level,
        options=context.options,
        iq=context.iq + bonus,
        will=context.will + bonus,
        per=context.per + bonus,
        ht=context.ht,
        target_will=context.target_will + target_resistance,
        target_ht=context.target_ht + target_resistance,
        mind_shield=context.mind_shield,
        channel=context.channel,
        interrupted=context.interrupted,
        unavailable=context.unavailable,
        shock=context.shock,
        build_revision=context.build_revision,
        held_item_ids=context.held_item_ids,
    )
