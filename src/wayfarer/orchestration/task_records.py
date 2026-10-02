"""Private B66 task choices, bound to the canonical campaign transaction."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer import validation
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import ActionResult, Inspect, PlayState, Social
from wayfarer.engine.simulation.campaign.activities import ActivityOutcome, LongTaskRule
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.traits.luck import LuckReceipt, LuckState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

STATE_PREFIX = "task-host:"
BINDING_PREFIX = "task-binding:"
RESULT_PREFIX = "task-result:"
CLOCK_PREFIX = "task-clock:"
PRIVATE_PREFIXES = (
    STATE_PREFIX,
    BINDING_PREFIX,
    RESULT_PREFIX,
    CLOCK_PREFIX,
    "campaign-long-task-v2:",
    "campaign-long-task-rest-v2:",
    "campaign-activity:",
)


def identity(prefix: str, value: str) -> str:
    return prefix + hashlib.sha256(value.encode()).hexdigest()


class BindLongTask(Command):
    kind: Literal["bind-long-task"] = "bind-long-task"
    rule: LongTaskRule
    location_id: Id
    required_equipment: Id | None = None
    supervisor_skill_id: Id | None = None


class BeginTaskCheck(Command):
    kind: Literal["begin-check"] = "begin-check"
    check_id: Id
    secret: bool = False


class PrepareSecretTaskCheck(Command):
    kind: Literal["prepare-secret-check"] = "prepare-secret-check"
    check_id: Id

    @property
    def secret(self) -> bool:
        return True


class ChooseSecretTaskCheck(Command):
    kind: Literal["choose-secret-check"] = "choose-secret-check"
    pending_id: Id
    choice: Literal["use-luck", "resolve", "cancel"]


class BeginTaskWork(Command):
    kind: Literal["begin-work"] = "begin-work"
    task_id: Id
    seconds: int = Field(gt=0, le=86400, multiple_of=3600)
    supervisor_actor_id: Id | None = None


class ChooseTaskCheck(Command):
    kind: Literal["accept-check", "use-luck"]
    pending_id: Id


class SetRealPlayClock(Command):
    kind: Literal["play-clock"] = "play-clock"
    running: bool


TaskCommand = Annotated[
    BindLongTask
    | BeginTaskCheck
    | BeginTaskWork
    | ChooseTaskCheck
    | SetRealPlayClock
    | PrepareSecretTaskCheck
    | ChooseSecretTaskCheck,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[TaskCommand] = TypeAdapter(TaskCommand)


class LongTaskBinding(Record):
    rule: LongTaskRule
    location_id: Id
    required_equipment: Id | None = None
    supervisor_skill_id: Id | None = None
    declared_by: Id
    campaign_id: Id


class TaskPending(Record):
    id: Id
    actor_id: Id
    original: CheckTrace
    opened_elapsed_microseconds: int = Field(ge=0)
    secret: bool = False
    # JSON is private family data, never a caller-supplied target or die roll.
    ordinary: Inspect | Social | None = None
    preparation_json: str


class SecretTaskPending(Record):
    kind: Literal["secret-unrolled"] = "secret-unrolled"
    id: Id
    actor_id: Id
    prepared_elapsed_microseconds: int = Field(ge=0)
    ordinary: Inspect | Social
    preparation_json: str


class TaskSnapshot(Record):
    campaign_id: Id
    pending: TaskPending | SecretTaskPending | None = None
    luck: LuckState = LuckState()


class TaskResult(Record):
    command_id: Id
    actor_id: Id
    status: Literal["bound", "clock", "pending", "completed", "interrupted", "cancelled"]
    pending_id: Id | None = None
    check: CheckTrace | None = None
    luck: LuckReceipt | None = None
    action: ActionResult | None = None
    activity: ActivityOutcome | None = None
    secret: bool = False
    reason: str = ""


def snapshot(state: PlayState) -> TaskSnapshot:
    last = next(
        (event for event in reversed(state.resources.events) if event.id.startswith(STATE_PREFIX)),
        None,
    )
    result = (
        TaskSnapshot(campaign_id=state.campaign_id)
        if last is None
        else TaskSnapshot.model_validate_json(last.kind)
    )
    if result.campaign_id != state.campaign_id:
        raise ValidationError("Task snapshot belongs to another campaign")
    pending = result.pending
    if result.luck.pending_roll_id != (pending.id if pending else None):
        raise ValidationError("Task and Luck pending identities disagree")
    if pending is not None:
        roll = next((roll for roll in result.luck.rolls if roll.id == pending.id), None)
        if roll is None or (roll.actor_id, roll.original, roll.secret, roll.chosen_dice) != (
            pending.actor_id,
            None if isinstance(pending, SecretTaskPending) else pending.original.dice,
            True if isinstance(pending, SecretTaskPending) else pending.secret,
            None,
        ):
            raise ValidationError("Task original does not match its Luck record")
    return result


def append_record(
    state: PlayState, prefix: str, command_id: str, target: str, record: Record
) -> PlayState:
    identifier = identity(prefix, command_id)
    if any(event.id == identifier for event in state.resources.events):
        raise ConflictError("Task record identity was already committed")
    event = ResourceEvent(
        id=identifier,
        at=state.resources.game_time,
        target_id=target,
        kind=record.model_dump_json(),
    )
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"events": state.resources.events + (event,)}
            )
        }
    )


def require_task_boundary(
    state: PlayState, pending_id: str | None, *, clock_only: bool = False
) -> None:
    """A table must accept or replace its last roll before anyone rolls again.

    Unrolled secret choices also admit ordinary GM resolution or cancellation.
    The persisted choice is itself a continuation; the trusted director can accept
    the recorded original when the player is absent. Clock pause/resume does not
    draw dice or reopen a choice which was unavailable when the original was rolled.
    """
    pending = snapshot(state).pending
    if pending is not None and pending.id != pending_id and not clock_only:
        raise ConflictError("Accept the pending task roll or use Luck before continuing play")


def has_task_records(raw: str) -> bool:
    """Only enrolled checkpoints need this family's aggregate validation.

    Resource-only commands retain their existing live-play refusal before trying
    to interpret a legacy presence marker as a complete PlayState. Inspect decoded
    event identities, including escaped JSON, rather than authored prose.
    """
    document = validation.mapping(validation.decode(raw))
    resources = document.get("resources")
    if not isinstance(resources, dict):
        return False
    events = validation.mapping(resources).get("events", [])
    if not isinstance(events, list):
        return False
    for event in validation.sequence(events):
        if not isinstance(event, dict):
            continue
        identifier = validation.mapping(event).get("id")
        if isinstance(identifier, str) and identifier.startswith(STATE_PREFIX):
            return True
    return False
