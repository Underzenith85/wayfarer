"""Atomic physiology intervals on the existing HP pool and event ledger."""

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.simulation.health.healing import restore_hp
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
    started: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)


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
    interval: PhysiologyInterval | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    request_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[PhysiologyEvent, ...]:
    return tuple(
        PhysiologyEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _request_digest(command: PhysiologyCommand, interval: PhysiologyInterval) -> str:
    return hashlib.sha256(
        (command.model_dump_json() + "\n" + interval.model_dump_json()).encode()
    ).hexdigest()


def _require_regeneration_due(
    resources: ResourceState, interval: PhysiologyInterval, period: int
) -> None:
    if interval.due < interval.started + period or (interval.due - interval.started) % period:
        raise ValidationError("Regeneration interval differs from the approved rate")
    consumed: list[int] = []
    for raw in resources.events:
        if not raw.id.startswith(PREFIX):
            continue
        entry = PhysiologyEvent.model_validate_json(raw.kind)
        if entry.outcome.actor_id != interval.actor_id or entry.outcome.kind != "regenerated":
            continue
        if entry.outcome.interval_id == interval.id:
            raise ConflictError("Regeneration interval was already consumed")
        # Old checkpoints did not record a due tick. Their settlement tick is a
        # conservative lower bound for scheduling the next legal interval.
        consumed.append(raw.at if entry.interval is None else entry.interval.due)
    if consumed and interval.due < max(consumed) + period:
        raise ConflictError("Regeneration interval was already consumed or is too early")


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
    digest = _request_digest(command, interval)
    prior = next((entry for entry in history(resources) if entry.command_id == command.id), None)
    if prior is not None:
        if (
            prior.outcome.actor_id == command.actor_id
            and prior.outcome.interval_id == command.interval_id
            and (prior.request_digest is None or prior.request_digest == digest)
        ):
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
        period = traits.regeneration_interval()
        if period is None:
            raise ValidationError("Regeneration requires an approved rate")
        purchase = traits.purchase(required)
        assert purchase is not None
        if "radiation-only" in purchase.modifiers:
            raise ValidationError("Radiation-only regeneration cannot restore HP")
        _require_regeneration_due(resources, interval, period)
        if hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Regeneration requires canonical Basic Set HP")
        # B424 explicitly scales Regeneration along with other HP healing.
        healed_hp, _ = restore_hp(
            resources, hp, traits.regeneration_amount() * max(1, hp.maximum // 10), kind="natural"
        )
        kind, after = "regenerated", healed_hp.current
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
    event = PhysiologyEvent(
        command_id=command.id, outcome=outcome, interval=interval, request_digest=digest
    )
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
