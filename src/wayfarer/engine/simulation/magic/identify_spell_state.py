"""Private B249 spell-identification commands, secret truth and apparent findings."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "identify-spell:"
PREFIXES = (PREFIX,)


class UnfamiliarSpell(Record):
    spell_id: Id
    description: str = Field(min_length=1, max_length=500)


class IdentifySubject(Record):
    id: Id
    caster_id: Id
    subject_id: Id
    touching: Literal[True] = True
    unfamiliar: tuple[UnfamiliarSpell, ...] = ()


class ObserveIdentifySpellSubject(Command):
    kind: Literal["identify_spell_subject"] = "identify_spell_subject"
    subject: IdentifySubject


class CastIdentifySpell(Command):
    kind: Literal["identify_spell_cast"] = "identify_spell_cast"
    cast_id: Id
    subject_id: Id


class ReportIdentifySpell(Command):
    kind: Literal["identify_spell_report"] = "identify_spell_report"
    cast_id: Id
    descriptions: tuple[str, ...] | None = None


IdentifySpellCommand = Annotated[
    ObserveIdentifySpellSubject | CastIdentifySpell | ReportIdentifySpell,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[IdentifySpellCommand] = TypeAdapter(IdentifySpellCommand)


class IdentifySubjectObservation(Record):
    command_id: Id
    subject: IdentifySubject
    location_id: Id


class ObservedSpell(Record):
    event_id: Id
    cast_id: Id
    spell_id: Id
    caster_id: Id
    subject_id: Id
    at: int
    status: Literal["casting", "completed"]
    description: str


class SecretIdentification(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    subject_id: Id
    at: int
    check: CheckTrace
    spells: tuple[ObservedSpell, ...]
    descriptions: tuple[str, ...]
    energy_spent: Literal[2] = 2


class IdentificationReport(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    subject_id: Id
    descriptions: tuple[str, ...]
    truthful: bool


class IdentifySpellResult(Record):
    command_id: Id
    outcome: Literal["accepted", "finished", "reported"]


class DirectorIdentifySpellResult(IdentifySpellResult):
    secret: SecretIdentification | None = None


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


def observations(resources: ResourceState) -> tuple[IdentifySubjectObservation, ...]:
    return tuple(
        IdentifySubjectObservation.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "subject:")
    )


def secrets(resources: ResourceState) -> tuple[SecretIdentification, ...]:
    return tuple(
        SecretIdentification.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "secret:")
    )


def reports(resources: ResourceState) -> tuple[IdentificationReport, ...]:
    return tuple(
        IdentificationReport.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX + "report:")
    )


def secret_result(resources: ResourceState, cast_id: str) -> SecretIdentification | None:
    return next((s for s in secrets(resources) if s.cast_id == cast_id), None)


def projection(
    resources: ResourceState, actor_ids: tuple[str, ...]
) -> tuple[dict[str, object], ...]:
    return tuple(
        {"actor_id": r.actor_id, "subject_id": r.subject_id, "descriptions": list(r.descriptions)}
        for r in reports(resources)
        if r.actor_id in actor_ids
    )
