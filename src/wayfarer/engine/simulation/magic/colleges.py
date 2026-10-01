"""Shared authority, randomness, privacy and receipt boundary for college spells."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.magic.colleges import CollegeSpellBinding
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.spell_transitions import SpellExecutionContext, reduce_spell
from wayfarer.engine.simulation.magic.spells import SPELLS, SpellCommand, SpellResult
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Record


class CollegeSpellCommand(Record):
    id: str
    actor_id: str
    expected_revision: int = Field(ge=0)
    build_revision: str
    spell_id: str
    target_actor_id: str | None = None
    target_item_id: str | None = None
    interrupted: bool = False


class CollegeSpellOutcome(Record):
    command_id: str
    actor_id: str
    spell_id: str
    target_actor_id: str | None = None
    target_item_id: str | None = None
    outcome: Literal["critical-success", "success", "failure", "critical-failure"]
    margin: int


def _digest(command: CollegeSpellCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def history(state: ResourceState) -> tuple[CollegeSpellOutcome, ...]:
    return tuple(
        CollegeSpellOutcome.model_validate_json(value.kind)
        for value in state.events
        if value.id.startswith("college-spell:")
    )


def visible_history(
    state: ResourceState, *, viewer_actor_id: str, gm: bool = False
) -> tuple[CollegeSpellOutcome, ...]:
    return tuple(value for value in history(state) if gm or value.actor_id == viewer_actor_id)


def apply_college_spell(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: CollegeSpellCommand,
    bindings: tuple[CollegeSpellBinding, ...],
    *,
    authorized_actor_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, CollegeSpellOutcome]:
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Spell actor lacks authority")
    digest = _digest(command)
    previous = next((value for value in state.receipts if value.command_id == command.id), None)
    if previous is not None:
        if previous.digest != digest:
            raise ConflictError("Spell command ID was reused with different intent")
        outcome = next((value for value in history(state) if value.command_id == command.id), None)
        if outcome is None:
            raise ValidationError("Spell receipt has no matching outcome")
        return state, outcome
    if command.expected_revision != state.revision:
        raise ConflictError("Spell revision conflict")
    if command.build_revision != build.revision:
        raise ValidationError("Spell build approval changed")
    if command.interrupted:
        raise ConflictError("Spell concentration was interrupted")
    allowed = {value.id for value in bindings}
    if command.spell_id not in allowed:
        raise ValidationError("Spell is outside the selected college package")
    entities = {value.id: value for value in world.entities}
    actor = entities.get(command.actor_id)
    target = entities.get(command.target_actor_id) if command.target_actor_id else None
    if actor is None or actor.kind is not EntityKind.ACTOR:
        raise ValidationError("Spell actor is unavailable")
    if command.target_actor_id is not None and (
        target is None
        or target.kind is not EntityKind.ACTOR
        or target.location_id != actor.location_id
    ):
        raise ValidationError("Spell target is unavailable")
    if command.target_item_id is not None:
        item = next((value for value in state.items if value.id == command.target_item_id), None)
        if item is None or item.owner_id != command.actor_id:
            raise AuthorizationError("Spell target item is not controlled by the actor")
    levels = {value.target: int(value.value) for value in build.sheet.values}
    level = levels.get(command.spell_id)
    if level is None:
        raise ValidationError("Approved build does not know this spell")
    raise ValidationError(
        "College check has no executable effect; use an authoritative spell channel"
    )


def dispatch_college_spell(
    runtime: RulesContext,
    state: PlayState,
    command: SpellCommand,
    bindings: tuple[CollegeSpellBinding, ...],
    *,
    authorized_actor_id: str,
) -> tuple[PlayState, SpellResult]:
    """One source-reviewed reducer owns time, energy and typed concrete effects.

    College identity narrows the allowed spells; campaign-authored channels own
    targets and modifiers, and approved builds own trained skill. Unsupported
    effects have no SpellCommand variant and cannot reach a successful check.
    """
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Spell actor lacks authority")
    if "spell:" + command.spell_id not in {binding.id for binding in bindings}:
        raise ValidationError("Spell is outside the selected college package")
    if command.spell_id not in SPELLS:
        raise ValidationError("College spell has no executable effect")
    rules = runtime.rules.spells
    if rules is None or rules.execution_version != 2:
        raise ValidationError("College casts require source-reviewed execution version 2")
    return reduce_spell(state, command, SpellExecutionContext(runtime))
