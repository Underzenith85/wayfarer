"""Trusted physical lock transitions; transaction policy belongs to the host."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.bindings import RuntimeBackfireAlternative
from wayfarer.engine.simulation.magic.lock_backfires import declare as declare_backfire
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel, declare
from wayfarer.engine.simulation.magic.lock_ready import require_free_hand
from wayfarer.engine.simulation.magic.lock_state import (
    LockState,
    destroyed,
    latest,
    opening_allowed,
    save,
    validate_fixture,
)
from wayfarer.engine.simulation.resources import Advance, Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "lock-host:"


class DeclareLock(Command):
    kind: Literal["declare-lock"] = "declare-lock"
    state: LockState


class DeclareLockChannel(Command):
    kind: Literal["declare-lock-channel"] = "declare-lock-channel"
    channel: LockChannel


class OperateLock(Command):
    kind: Literal["operate-lock"] = "operate-lock"
    target_id: Id
    operation: Literal["open", "close"]


class DeclareLockBackfire(Command):
    kind: Literal["declare-lock-backfire"] = "declare-lock-backfire"
    choice: RuntimeBackfireAlternative


LockCommand = Annotated[
    DeclareLock | DeclareLockChannel | OperateLock | DeclareLockBackfire,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[LockCommand] = TypeAdapter(LockCommand)


class LockReceipt(Record):
    command_id: Id
    outcome: Literal["declared", "opened", "closed", "destroyed"]
    object_id: Id
    game_time: int


def _receipt_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _operate(
    runtime: RulesContext, before: PlayState, command: OperateLock
) -> tuple[PlayState, str]:
    require_innate_actor_action(before, command.actor_id)
    synchronous(before, command.actor_id)
    guard(before, command.actor_id, "lock")
    runtime.approved_build(before, command.actor_id)
    actor = next(a for a in before.actors if a.actor_id == command.actor_id)
    hp = next(p for p in before.resources.pools if p.id == "hp:" + command.actor_id)
    fp = next(p for p in before.resources.pools if p.id == "fp:" + command.actor_id)
    if (
        actor.conditions
        or actor.available_at > before.resources.game_time
        or hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or fp.fatigue is None
        or fp.fatigue.collapsed
        or fp.fatigue.unconscious
    ):
        raise ValidationError("Actor cannot manipulate a lock")
    if any(e.status == "active" and command.actor_id in e.turn_order for e in before.encounters):
        raise ValidationError("Physical door operation in combat requires a Ready adapter")
    value = latest(before.resources).get(command.target_id)
    if value is None:
        raise ValidationError("Unknown lock fixture")
    validate_fixture(before.world, before.resources, value.fixture)
    entity = next(e for e in before.world.entities if e.id == command.actor_id)
    if entity.location_id not in (
        value.fixture.passage or (value.fixture.location_id,)
    ) or command.target_id not in {
        e.id for e in before.world.perspective(command.actor_id).entities
    } | set(actor.aware_of):
        raise ValidationError("Lock is not reachable and perceived")
    require_free_hand(before.resources, command.actor_id, actor.held_item_hands)
    if destroyed(before.resources, value):
        return before, "destroyed"
    if command.operation == "open" and not opening_allowed(before.resources, value):
        raise ConflictError("The lock prevents opening")
    value = LockState.model_validate(
        {
            **value.model_dump(),
            "closed": command.operation == "close",
            "locked": command.operation == "close"
            if value.fixture.kind == "lock"
            else value.locked,
        }
    )
    resources = interrupt_concentration(before.resources, command.actor_id, command.id)
    before = runtime.advance(
        before.model_copy(update={"resources": resources}),
        Advance(
            id="lock-time:" + command.id,
            actor_id=command.actor_id,
            expected_revision=resources.revision,
            to=resources.game_time + 1,
        ),
    )
    resources = save(before.resources, value, command.id)
    return before.model_copy(
        update={
            "resources": resources,
            "party": before.party.model_copy(
                update={
                    "groups": tuple(
                        g.model_copy(update={"ready_through": resources.game_time})
                        for g in before.party.groups
                    )
                }
            ),
        }
    ), "opened" if command.operation == "open" else "closed"


def apply_host(
    runtime: RulesContext, before: PlayState, command: LockCommand
) -> tuple[PlayState, LockReceipt]:
    outcome = "declared"
    if isinstance(command, DeclareLock):
        value = command.state
        if value.fixture.object_id in latest(before.resources):
            raise ConflictError("A declared lock fixture cannot be replaced")
        validate_fixture(before.world, before.resources, value.fixture, declaring=True)
        updated = before.model_copy(update={"resources": save(before.resources, value, command.id)})
        object_id = value.fixture.object_id
    elif isinstance(command, DeclareLockChannel):
        updated = before.model_copy(
            update={"resources": declare(runtime, before, command.channel, command.id)}
        )
        object_id = command.channel.target_id
    elif isinstance(command, DeclareLockBackfire):
        updated = before.model_copy(
            update={"resources": declare_backfire(before.resources, command.choice, command.id)}
        )
        object_id = command.choice.target_ids[0]
    else:
        updated, outcome = _operate(runtime, before, command)
        object_id = command.target_id
    receipt = LockReceipt.model_validate(
        dict(
            command_id=command.id,
            outcome=outcome,
            object_id=object_id,
            game_time=updated.resources.game_time,
        )
    )
    resources = updated.resources.model_copy(
        update={
            "revision": before.revision + 1,
            "events": updated.resources.events
            + (
                ResourceEvent(
                    id=_receipt_id(command.id),
                    at=updated.resources.game_time,
                    target_id=command.actor_id,
                    kind=receipt.model_dump_json(),
                ),
            ),
        }
    )
    updated = updated.model_copy(update={"revision": before.revision + 1, "resources": resources})
    return updated, receipt
