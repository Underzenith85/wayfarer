"""Private complete Aura observations, secret checks and apparent findings."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "aura:"
PREFIXES = (PREFIX,)


class SecretAuraFact(Record):
    id: Id
    description: str = Field(min_length=1, max_length=500)
    definition_id: Id | None = None


class AuraSubjectFacts(Record):
    id: Id
    caster_id: Id
    subject_id: Id
    touching: Literal[True] = True
    classification: Literal["living-human"]
    classification_complete: Literal[True]
    personality_complete: Literal[True]
    personality: str = Field(min_length=1, max_length=1000)
    emotion_complete: Literal[True]
    violent_emotion: str | None = Field(default=None, min_length=1, max_length=500)
    secrets_complete: Literal[True]
    secret_traits: tuple[SecretAuraFact, ...] = ()
    mage_power: str | None = Field(default=None, min_length=1, max_length=500)


class ObserveAuraSubject(Command):
    kind: Literal["aura_subject"] = "aura_subject"
    subject: AuraSubjectFacts


class CastAura(Command):
    kind: Literal["aura_cast"] = "aura_cast"
    cast_id: Id
    subject_id: Id


class AuraFinding(Record):
    personality: str = Field(min_length=1, max_length=1000)
    mage: bool
    mage_power: str | None = Field(default=None, min_length=1, max_length=500)
    controlled: bool
    possessed: bool
    violent_emotion: str | None = Field(default=None, min_length=1, max_length=500)
    secret_traits: tuple[str, ...] = ()


class ReportAura(Command):
    kind: Literal["aura_report"] = "aura_report"
    cast_id: Id
    personality: str | None = Field(default=None, min_length=1, max_length=1000)
    false_finding: AuraFinding | None = None


AuraCommand = Annotated[ObserveAuraSubject | CastAura | ReportAura, Field(discriminator="kind")]
ADAPTER: TypeAdapter[AuraCommand] = TypeAdapter(AuraCommand)


class AuraSubjectObservation(Record):
    command_id: Id
    subject: AuraSubjectFacts
    location_id: Id
    entity_json: str
    body_json: str
    build_digest: str
    magery: int
    control_json: tuple[str, ...]
    conditions: tuple[str, ...]
    world_facts_json: str


class SecretAura(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    subject_id: Id
    at: int
    check: CheckTrace
    observation: AuraSubjectObservation
    energy_spent: Literal[3] = 3


class AuraReport(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    subject_id: Id
    finding: AuraFinding | None
    truthful: bool


class AuraResult(Record):
    command_id: Id
    outcome: Literal["accepted", "finished", "reported"]


class DirectorAuraResult(AuraResult):
    secret: SecretAura | None = None


def identifier(label: str, command_id: str) -> str:
    return PREFIX + label + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def append(
    resources: ResourceState, label: str, command_id: str, actor_id: str, value: Record
) -> ResourceState:
    event = ResourceEvent(
        id=identifier(label, command_id),
        at=resources.game_time,
        kind=value.model_dump_json(),
        target_id=actor_id,
    )
    return resources.model_copy(update={"events": resources.events + (event,)})


def observations(resources: ResourceState) -> tuple[AuraSubjectObservation, ...]:
    return tuple(
        AuraSubjectObservation.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "subject:")
    )


def secrets(resources: ResourceState) -> tuple[SecretAura, ...]:
    return tuple(
        SecretAura.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "secret:")
    )


def secret_result(resources: ResourceState, cast_id: str) -> SecretAura | None:
    return next((s for s in secrets(resources) if s.cast_id == cast_id), None)


def reports(resources: ResourceState) -> tuple[AuraReport, ...]:
    return tuple(
        AuraReport.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "report:")
    )


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "actor_id": r.actor_id,
            "subject_id": r.subject_id,
            "finding": r.finding.model_dump(mode="json") if r.finding is not None else None,
        }
        for r in reports(resources)
        if r.actor_id in actor_ids
    )
