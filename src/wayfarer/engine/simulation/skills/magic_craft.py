"""Bounded, authoritative magic-craft checks on the shared resource ledger."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import RandomSource, success_check
from wayfarer.engine.rules.skills.magic_craft import BINDING_BY_ID, PACKAGE_ID
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Record

VERSION = "1.0.0"


class MagicCraftCommand(Record):
    id: str
    actor_id: str
    expected_revision: int = Field(ge=0)
    build_revision: str
    skill_id: str
    mode: Literal[
        "analyze",
        "prepare",
        "locate",
        "identify",
        "invoke",
        "inscribe-focus",
        "inscribe-potency",
        "research",
    ]
    subject_item_id: str | None = None
    interrupted: bool = False


class MagicCraftOutcome(Record):
    command_id: str
    actor_id: str
    skill_id: str
    mode: str
    subject_item_id: str | None = None
    outcome: Literal["critical-success", "success", "failure", "critical-failure"]
    margin: int


def _digest(command: MagicCraftCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _event(command_id: str) -> str:
    return "magic-craft:" + hashlib.sha256(command_id.encode()).hexdigest()


def history(state: ResourceState) -> tuple[MagicCraftOutcome, ...]:
    return tuple(
        MagicCraftOutcome.model_validate_json(value.kind)
        for value in state.events
        if value.id.startswith("magic-craft:")
    )


def visible_history(
    state: ResourceState, *, viewer_actor_id: str, gm: bool = False
) -> tuple[MagicCraftOutcome, ...]:
    return tuple(value for value in history(state) if gm or value.actor_id == viewer_actor_id)


def apply_magic_craft(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: MagicCraftCommand,
    *,
    authorized_actor_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, MagicCraftOutcome]:
    digest = _digest(command)
    previous = next((value for value in state.receipts if value.command_id == command.id), None)
    if previous is not None:
        if previous.digest != digest:
            raise ConflictError("Magic-craft command ID was reused with different intent")
        outcome = next((value for value in history(state) if value.command_id == command.id), None)
        if outcome is None:
            raise ValidationError("Magic-craft receipt has no outcome")
        return state, outcome
    if command.expected_revision != state.revision:
        raise ConflictError("Magic-craft revision conflict")
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Magic-craft actor lacks authority")
    if command.build_revision != build.revision:
        raise ValidationError("Magic-craft build approval changed")
    if command.interrupted:
        raise ConflictError("Magic-craft procedure was interrupted")
    binding = BINDING_BY_ID.get(command.skill_id)
    if binding is None or command.mode not in binding.modes:
        raise ValidationError("Unsupported magic-craft procedure")
    entities = {entity.id: entity for entity in world.entities}
    actor = entities.get(command.actor_id)
    if actor is None or actor.kind is not EntityKind.ACTOR:
        raise ValidationError("Magic-craft actor is unavailable")
    if command.subject_item_id is not None:
        item = next((value for value in state.items if value.id == command.subject_item_id), None)
        if item is None or item.owner_id != command.actor_id:
            raise AuthorizationError("Magic-craft subject item is not controlled by the actor")
    elif command.mode in {"analyze", "prepare", "inscribe-focus", "inscribe-potency"}:
        raise ValidationError("Magic-craft procedure requires an authored subject item")
    levels = {value.target: int(value.value) for value in build.sheet.values}
    level = levels.get(command.skill_id)
    if level is None:
        raise ValidationError("Approved build does not know this magic-craft skill")
    trace = success_check(
        level,
        rng=rng,
        rules_package=PACKAGE_ID,
        rules_version=VERSION,
    )
    outcome = MagicCraftOutcome(
        command_id=command.id,
        actor_id=command.actor_id,
        skill_id=command.skill_id,
        mode=command.mode,
        subject_item_id=command.subject_item_id,
        outcome=trace.outcome.value,
        margin=trace.margin,
    )
    return (
        state.model_copy(
            update={
                "revision": state.revision + 1,
                "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
                "events": state.events
                + (
                    ResourceEvent(
                        id=_event(command.id),
                        at=state.game_time,
                        kind=outcome.model_dump_json(),
                        target_id=command.actor_id,
                    ),
                ),
            }
        ),
        outcome,
    )
