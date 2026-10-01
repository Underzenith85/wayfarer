"""Private durable exposure, satisfaction and calendar facts for B130/B161."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.engine.simulation.traits.physiology_calendar import PhysiologyCalendar
from wayfarer.engine.simulation.traits.physiology_types import PhysiologyOutcome
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "harmful-physiology:"
HarmfulKind = Literal["weakness", "dependency"]


class ObservePhysiology(Command):
    """Director declaration at the current clock; a dose means actual administration.

    A new missing dependency starts overdue now. A new present dose starts its
    purchased grace period now. Contact must accumulate its full source duration.
    """

    kind: Literal["observe-physiology"] = "observe-physiology"
    source: HarmfulKind
    condition_id: Id
    present: bool
    dependency_mode: Literal["dose", "contact"] = "dose"


class ReconcilePhysiology(Command):
    kind: Literal["reconcile-physiology"] = "reconcile-physiology"
    source: HarmfulKind


class SettlePhysiology(Command):
    kind: Literal["settle-physiology"] = "settle-physiology"
    interval_id: Id


class AdvancePhysiology(Command):
    kind: Literal["advance-physiology"] = "advance-physiology"
    to: int = Field(ge=0)


class DeclarePhysiologyCalendar(Command):
    kind: Literal["physiology-calendar"] = "physiology-calendar"
    calendar: PhysiologyCalendar


HarmfulCommand = Annotated[
    ObservePhysiology
    | ReconcilePhysiology
    | SettlePhysiology
    | AdvancePhysiology
    | DeclarePhysiologyCalendar,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER: TypeAdapter[HarmfulCommand] = TypeAdapter(HarmfulCommand)


class HarmfulCondition(Record):
    actor_id: Id
    source: HarmfulKind
    condition_id: Id
    purchase_digest: str
    frequency: str
    dependency_mode: Literal["dose", "contact"]
    present: bool
    started: int = Field(ge=0)
    generation: Id
    # None means the required substance is currently continuously supplied.
    dependency_due: int | None = Field(default=None, ge=0)
    due: int | None = Field(default=None, ge=0)
    contact_seconds: int = Field(default=0, ge=0)
    contact_started: int | None = Field(default=None, ge=0)
    contact_due: int | None = Field(default=None, ge=0)
    retired: bool = False

    @property
    def deadline(self) -> int | None:
        values = [v for v in (self.due, self.contact_due) if v is not None]
        return min(values) if values else None

    @property
    def interval_id(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.actor_id, self.source, self.condition_id, self.generation, self.deadline]
            ).encode()
        ).hexdigest()


class HarmfulReceipt(Record):
    command_id: Id
    conditions: tuple[HarmfulCondition, ...] = ()
    calendar: PhysiologyCalendar | None = None
    injuries: tuple[PhysiologyOutcome, ...] = ()
    game_time: int = Field(ge=0)


def history(state: ResourceState) -> tuple[HarmfulReceipt, ...]:
    return tuple(
        HarmfulReceipt.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def conditions(state: ResourceState) -> tuple[HarmfulCondition, ...]:
    latest: dict[tuple[str, HarmfulKind], HarmfulCondition] = {}
    for receipt in history(state):
        for item in receipt.conditions:
            latest[item.actor_id, item.source] = item
    return tuple(latest.values())


def calendar(state: ResourceState) -> PhysiologyCalendar | None:
    return next((r.calendar for r in reversed(history(state)) if r.calendar is not None), None)


def living(state: ResourceState, item: HarmfulCondition) -> bool:
    hp = next((pool for pool in state.pools if pool.id == "hp:" + item.actor_id), None)
    return hp is not None and hp.injury is not None and not hp.injury.dead


def require_deadline(state: ResourceState, to: int, *, actor_id: str | None = None) -> None:
    if any(
        not item.retired
        and (actor_id is None or item.actor_id == actor_id)
        and living(state, item)
        and item.deadline is not None
        and item.deadline < to
        for item in conditions(state)
    ):
        raise ConflictError("Settle the harmful physiology deadline before continuing")


def require_no_transformation_bindings(state: ResourceState, actor_ids: frozenset[str]) -> None:
    if any(not item.retired and item.actor_id in actor_ids for item in conditions(state)):
        raise ValidationError("Harmful physiology and transformations require supported ordering")
