"""Event-backed switching and spell-environment projection for mana traits."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.mana_divine import (
    MANA_LEVELS,
    ManaDivineTraits,
    mana_divine_traits,
)
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.simulation.magic.spells import SpellContext
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState, Scheduled
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "mana-field:"
FIELD_DEFINITIONS = frozenset({"advantage:mana-damper", "advantage:mana-enhancer"})


class ManaField(Record):
    id: str
    definition_id: str
    actor_id: str
    location_id: str
    duration_seconds: int | None = Field(default=None, ge=1)


class ManaFieldCommand(Command):
    field_id: str
    action: Literal["activate", "deactivate"]


class ManaFieldOutcome(Record):
    action: Literal["activated", "deactivated"]
    field_id: str
    definition_id: str
    effect_id: str
    expires_at: int | None = Field(default=None, ge=0)


class ManaFieldEvent(Record):
    command_id: str
    outcome: ManaFieldOutcome


def _id(value: str, purpose: str) -> str:
    return PREFIX + purpose + ":" + hashlib.sha256(value.encode()).hexdigest()


def _effect_id(field_id: str) -> str:
    return _id(field_id, "effect")


def history(resources: ResourceState) -> tuple[ManaFieldEvent, ...]:
    return tuple(
        ManaFieldEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX + "event:")
    )


def _area_level(modifiers: tuple[str, ...]) -> int:
    selected = tuple(
        int(value.removeprefix("area-effect-"))
        for value in modifiers
        if value.startswith("area-effect-")
    )
    return 0 if not selected else selected[0]


def apply_mana_field(
    resources: ResourceState,
    world: World,
    command: ManaFieldCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    fields: tuple[ManaField, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, ManaFieldOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Mana field requires actor authority")
    previous = next((event for event in history(resources) if event.command_id == command.id), None)
    if previous is not None:
        if previous.outcome.field_id == command.field_id:
            return resources, previous.outcome
        raise ConflictError("Mana-field command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Mana-field revision changed")
    field = next((value for value in fields if value.id == command.field_id), None)
    if (
        field is None
        or field.actor_id != command.actor_id
        or field.definition_id not in FIELD_DEFINITIONS
    ):
        raise ValidationError("Authored mana field is unavailable")
    entities = {entity.id: entity for entity in world.entities}
    actor = entities.get(command.actor_id)
    location = entities.get(field.location_id)
    if (
        actor is None
        or actor.location_id != field.location_id
        or location is None
        or location.kind is not EntityKind.LOCATION
    ):
        raise ValidationError("Mana-field context changed")
    traits = mana_divine_traits(build, definitions)
    purchase = traits.purchase(field.definition_id)
    if purchase is None or "switchable" not in purchase.modifiers:
        raise ValidationError("Mana field is not switchable in the approved build")
    effect_id = _effect_id(field.id)
    active = effect_id in resources.active_effect_ids
    if command.action == "activate" and active:
        raise ConflictError("Mana field is already active")
    if command.action == "deactivate" and not active:
        raise ConflictError("Mana field is not active")
    effects = set(resources.active_effect_ids)
    scheduled = tuple(value for value in resources.scheduled if value.target_id != effect_id)
    expires_at = None
    if command.action == "activate":
        effects.add(effect_id)
        if field.duration_seconds is not None:
            expires_at = resources.game_time + field.duration_seconds
            scheduled += (
                Scheduled(
                    id=_id(field.id, "expiry"),
                    due=expires_at,
                    kind="expire",
                    target_id=effect_id,
                ),
            )
        action: Literal["activated", "deactivated"] = "activated"
    else:
        effects.remove(effect_id)
        action = "deactivated"
    outcome = ManaFieldOutcome(
        action=action,
        field_id=field.id,
        definition_id=field.definition_id,
        effect_id=effect_id,
        expires_at=expires_at,
    )
    event = ManaFieldEvent(command_id=command.id, outcome=outcome)
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
                    target_id=field.location_id,
                ),
            ),
        }
    )
    return state, outcome


def effective_mana(
    resources: ResourceState,
    world: World,
    builds: Mapping[str, ValidatedBuild],
    definitions: Mapping[str, RuleDefinition],
    fields: tuple[ManaField, ...],
    *,
    actor_id: str,
    location_id: str,
    base: ManaLevel,
) -> ManaLevel:
    """Resolve carried and same-location area fields for an existing spell context."""
    entities = {entity.id: entity for entity in world.entities}
    subject = entities.get(actor_id)
    location = entities.get(location_id)
    if (
        subject is None
        or subject.location_id != location_id
        or location is None
        or location.kind is not EntityKind.LOCATION
    ):
        raise ValidationError("Mana environment context changed")
    dampers: list[int] = []
    enhancers: list[int] = []
    for field in fields:
        owner = entities.get(field.actor_id)
        build = builds.get(field.actor_id)
        if owner is None or build is None or owner.location_id != field.location_id:
            continue
        traits = mana_divine_traits(build, definitions)
        purchase = traits.purchase(field.definition_id)
        if purchase is None:
            continue
        switchable = "switchable" in purchase.modifiers
        if switchable and _effect_id(field.id) not in resources.active_effect_ids:
            continue
        affects_subject = field.actor_id == actor_id or (
            field.location_id == location_id and _area_level(purchase.modifiers) > 0
        )
        if not affects_subject:
            continue
        target = dampers if field.definition_id == "advantage:mana-damper" else enhancers
        target.append(purchase.levels)
    shift = (max(enhancers) if enhancers else 0) - (max(dampers) if dampers else 0)
    index = max(0, min(len(MANA_LEVELS) - 1, MANA_LEVELS.index(base) + shift))
    return MANA_LEVELS[index]


def apply_spell_context(
    context: SpellContext,
    traits: ManaDivineTraits,
    *,
    college: str,
    mana: ManaLevel,
) -> SpellContext:
    """Bind approved mana-trait inputs into the canonical spell context."""
    if not traits.may_cast_magic():
        raise ValidationError("Approved mana traits prohibit spell casting")
    return context.model_copy(
        update={
            "magery": traits.magery_level() if traits.has_magery() else context.magery,
            "skill": context.skill + traits.magery_bonus(college=college),
            "mana": mana,
        }
    )
