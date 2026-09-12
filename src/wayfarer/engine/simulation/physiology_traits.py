"""Atomic physiology intervals on the existing HP pool and event ledger."""

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.physiology_traits import physiology_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "physiology:"


class PhysiologyInterval(Record):
    id: str
    actor_id: str
    kind: Literal["regeneration", "dependency", "weakness", "extra-life"]
    due: int = Field(ge=0)
    amount: int = Field(default=1, ge=1, le=1000)


class PhysiologyCommand(Command):
    interval_id: str


class PhysiologyOutcome(Record):
    actor_id: str
    kind: Literal["regenerated", "injured", "revived", "unavailable"]
    hp_before: int
    hp_after: int
    interval_id: str


class PhysiologyEvent(Record):
    command_id: str
    outcome: PhysiologyOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[PhysiologyEvent, ...]:
    return tuple(
        PhysiologyEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def apply_physiology_interval(
    resources: ResourceState,
    command: PhysiologyCommand,
    interval: PhysiologyInterval,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    *,
    authorized_actor_id: str,
    system: bool = False,
) -> tuple[ResourceState, PhysiologyOutcome]:
    if (
        not system
        or authorized_actor_id != command.actor_id
        or interval.actor_id != command.actor_id
    ):
        raise ValidationError("Physiology execution requires actor authority")
    prior = next((entry for entry in history(resources) if entry.command_id == command.id), None)
    if prior is not None:
        if prior.outcome.interval_id == command.interval_id:
            return resources, prior.outcome
        raise ConflictError("Physiology command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Physiology revision changed")
    if interval.id != command.interval_id or resources.game_time < interval.due:
        raise ValidationError("Physiology interval is not due")
    traits = physiology_traits(build, definitions)
    required = {
        "regeneration": "advantage:regeneration",
        "dependency": "disadvantage:dependency",
        "weakness": "disadvantage:weakness",
        "extra-life": "advantage:extra-life",
    }[interval.kind]
    hp = next((pool for pool in resources.pools if pool.id == "hp:" + command.actor_id), None)
    if hp is None:
        raise ValidationError("Physiology HP pool is unavailable")
    before = hp.current
    if not traits.has(required):
        kind: Literal["regenerated", "injured", "revived", "unavailable"] = "unavailable"
        after = before
    elif interval.kind == "regeneration":
        kind, after = "regenerated", min(hp.maximum, before + interval.amount)
    elif interval.kind in {"dependency", "weakness"}:
        kind, after = "injured", before - interval.amount
    else:
        used = sum(
            1
            for entry in history(resources)
            if entry.outcome.kind == "revived" and entry.outcome.actor_id == command.actor_id
        )
        if before > -hp.maximum or used >= traits.level("advantage:extra-life"):
            kind, after = "unavailable", before
        else:
            kind, after = "revived", hp.maximum
    outcome = PhysiologyOutcome(
        actor_id=command.actor_id,
        kind=kind,
        hp_before=before,
        hp_after=after,
        interval_id=interval.id,
    )
    pools = tuple(
        pool if pool.id != hp.id else pool.model_copy(update={"current": after})
        for pool in resources.pools
    )
    event = PhysiologyEvent(command_id=command.id, outcome=outcome)
    return resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "pools": pools,
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    ), outcome
