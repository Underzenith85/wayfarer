"""Private B249 Regular casting and observer-bound detection records."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "detect-magic:"
PREFIXES = (PREFIX,)


class DetectSubject(Record):
    id: Id
    caster_id: Id
    target_id: Id
    carrier: Literal["inventory", "lock"]
    touching: Literal[True] = True
    familiar: Literal[True] = True
    unconcealed: Literal[True] = True
    backfire: Literal["injury-one"]


class ObserveDetectMagicSubject(Command):
    kind: Literal["detect_magic_subject"] = "detect_magic_subject"
    subject: DetectSubject


class StartDetectMagic(Command):
    kind: Literal["detect_magic_start"] = "detect_magic_start"
    cast_id: Id
    subject_id: Id


class WorkDetectMagic(Command):
    kind: Literal["detect_magic_work"] = "detect_magic_work"
    cast_id: Id
    seconds: int = Field(ge=1, le=5)


class CompleteDetectMagic(Command):
    kind: Literal["detect_magic_complete"] = "detect_magic_complete"
    cast_id: Id


class CancelDetectMagic(Command):
    kind: Literal["detect_magic_cancel"] = "detect_magic_cancel"
    cast_id: Id


DetectMagicCommand = Annotated[
    ObserveDetectMagicSubject
    | StartDetectMagic
    | WorkDetectMagic
    | CompleteDetectMagic
    | CancelDetectMagic,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[DetectMagicCommand] = TypeAdapter(DetectMagicCommand)


class DetectObservation(Record):
    command_id: Id
    subject: DetectSubject
    physical_digest: str
    location_id: Id


class DetectFinding(Record):
    actor_id: Id
    target_id: Id
    magical: bool
    permanence: Literal["temporary", "permanent"] | None = None
    spell_id: Id | None = None
    power: int | None = None


class Detection(DetectFinding):
    identity: str
    binding_id: Id | None = None
    project_id: Id | None = None


class DetectCast(Record):
    cast_id: Id
    actor_id: Id
    subject_id: Id
    physical_digest: str
    magic_identity: str
    location_id: Id
    proposal_digest: str
    skill: int
    started_at: int
    last_at: int
    seconds: int = Field(default=0, ge=0, le=5)
    distracted: bool = False
    status: Literal["casting", "ready", "cancelled", "rolled"] = "casting"


class DetectResult(Record):
    command_id: Id
    outcome: Literal["accepted", "working", "cancelled", "finished"]
    check: CheckTrace | None = None
    energy_spent: int = 0
    finding: DetectFinding | None = None


def identifier(kind: str, command_id: str) -> str:
    return PREFIX + kind + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def append(
    state: ResourceState, kind: str, command_id: str, actor_id: str, value: Record
) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=identifier(kind, command_id),
                    at=state.game_time,
                    target_id=actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def observations(state: ResourceState) -> tuple[DetectObservation, ...]:
    return tuple(
        DetectObservation.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "observation:")
    )


def casts(state: ResourceState) -> dict[str, DetectCast]:
    found: dict[str, DetectCast] = {}
    for event in state.events:
        if event.id.startswith(PREFIX + "cast:"):
            value = DetectCast.model_validate_json(event.kind)
            found[value.cast_id] = value
    return found


def findings(state: ResourceState) -> tuple[Detection, ...]:
    return tuple(
        Detection.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "finding:")
    )


def pending_actor_ids(state: ResourceState) -> frozenset[str]:
    return frozenset(c.actor_id for c in casts(state).values() if c.status in ("casting", "ready"))


def interrupt_casts(
    state: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    for cast in casts(state).values():
        if cast.actor_id == actor_id and cast.status in ("casting", "ready"):
            value = cast.model_copy(
                update={
                    "distracted": distraction,
                    "status": cast.status if distraction else "cancelled",
                }
            )
            state = append(state, "cast", command_id + ":" + cast.cast_id, actor_id, value)
    return state


def projection(state: ResourceState, actor_ids: frozenset[str]) -> tuple[dict[str, object], ...]:
    return tuple(
        DetectFinding.model_validate(
            f.model_dump(include=set(DetectFinding.model_fields))
        ).model_dump(mode="json", exclude_none=True)
        for f in findings(state)
        if f.actor_id in actor_ids
    )


def apparent_power(
    state: ResourceState, actor_id: str, item_id: str, binding_id: str, project_id: str, power: int
) -> int | None:
    return next(
        (
            f.power
            for f in reversed(findings(state))
            if (f.actor_id, f.target_id, f.binding_id, f.project_id, f.power)
            == (actor_id, item_id, binding_id, project_id, power)
        ),
        None,
    )


def needs_clock_checkpoints(state: ResourceState) -> bool:
    return bool(pending_actor_ids(state))
