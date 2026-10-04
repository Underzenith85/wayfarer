"""Private B249 analysis work, secret outcomes and observer-specific knowledge."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.magic.protocols import MagicItemInstance
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.models import Id, Record

PREFIX = "analyze-magic:"
PREFIXES = (PREFIX,)


class AnalyzeSubject(Record):
    id: Id
    caster_id: Id
    item_id: Id
    touching: Literal[True] = True
    familiar: Literal[True] = True
    unconcealed: Literal[True] = True


class ObserveAnalyzeMagicSubject(Command):
    kind: Literal["analyze_magic_subject"] = "analyze_magic_subject"
    subject: AnalyzeSubject


class StartAnalyzeMagic(Command):
    kind: Literal["analyze_magic_start"] = "analyze_magic_start"
    cast_id: Id
    subject_id: Id


class WorkAnalyzeMagic(Command):
    kind: Literal["analyze_magic_work"] = "analyze_magic_work"
    cast_id: Id
    seconds: int = Field(ge=1, le=3600)


class CompleteAnalyzeMagic(Command):
    kind: Literal["analyze_magic_complete"] = "analyze_magic_complete"
    cast_id: Id


class CancelAnalyzeMagic(Command):
    kind: Literal["analyze_magic_cancel"] = "analyze_magic_cancel"
    cast_id: Id


class ReportAnalyzeMagic(Command):
    kind: Literal["analyze_magic_report"] = "analyze_magic_report"
    cast_id: Id
    claimed_power: int | None = Field(default=None, ge=1)


AnalyzeMagicCommand = Annotated[
    ObserveAnalyzeMagicSubject
    | StartAnalyzeMagic
    | WorkAnalyzeMagic
    | CompleteAnalyzeMagic
    | CancelAnalyzeMagic
    | ReportAnalyzeMagic,
    Field(discriminator="kind"),
]
ADAPTER: TypeAdapter[AnalyzeMagicCommand] = TypeAdapter(AnalyzeMagicCommand)


class SubjectObservation(Record):
    command_id: Id
    subject: AnalyzeSubject
    binding: MagicItemInstance
    item_digest: str
    definition_digest: str
    location_id: Id


class AnalysisCast(Record):
    cast_id: Id
    actor_id: Id
    subject_id: Id
    binding: MagicItemInstance
    item_digest: str
    definition_digest: str
    proposal_digest: str
    location_id: Id
    skill: int
    hp: int
    started_at: int
    last_at: int
    seconds: int = Field(default=0, ge=0, le=3600)
    distracted: bool = False
    status: Literal["casting", "ready", "cancelled", "rolled"] = "casting"


class AnalysisConcentrationReceipt(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    check: CheckTrace


class SecretAnalysis(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    item_id: Id
    binding: MagicItemInstance
    at: int
    check: CheckTrace
    energy_spent: int = 8


class AnalysisReport(Record):
    command_id: Id
    cast_id: Id
    actor_id: Id
    item_id: Id
    binding_id: Id
    project_id: Id
    spell_id: str
    actual_power: int
    claimed_power: int | None
    truthful: bool


class AnalyzeDiscovery(Record):
    actor_id: Id
    item_id: Id
    binding_id: Id
    project_id: Id
    power: int


class AnalyzeMagicResult(Record):
    command_id: Id
    outcome: Literal["accepted", "working", "finished", "cancelled", "reported"]


class DirectorAnalyzeMagicResult(AnalyzeMagicResult):
    secret: SecretAnalysis | None = Field(default=None, exclude_if=lambda v: v is None)


def identifier(label: str, command_id: str) -> str:
    return PREFIX + label + ":" + hashlib.sha256(command_id.encode()).hexdigest()


def append(
    state: ResourceState, label: str, command_id: str, actor_id: str, value: Record
) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=identifier(label, command_id),
                    at=state.game_time,
                    target_id=actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def observations(state: ResourceState) -> tuple[SubjectObservation, ...]:
    return tuple(
        SubjectObservation.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "subject:")
    )


def casts(state: ResourceState) -> dict[str, AnalysisCast]:
    return {
        c.cast_id: c
        for e in state.events
        if e.id.startswith(PREFIX + "cast:")
        for c in (AnalysisCast.model_validate_json(e.kind),)
    }


def secrets(state: ResourceState) -> tuple[SecretAnalysis, ...]:
    return tuple(
        SecretAnalysis.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "secret:")
    )


def secret_result(state: ResourceState, cast_id: str) -> SecretAnalysis | None:
    return next((s for s in secrets(state) if s.cast_id == cast_id), None)


def reports(state: ResourceState) -> tuple[AnalysisReport, ...]:
    return tuple(
        AnalysisReport.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(PREFIX + "report:")
    )


def pending_actor_ids(state: ResourceState) -> frozenset[str]:
    return frozenset(c.actor_id for c in casts(state).values() if c.status in ("casting", "ready"))


def discovery_records(state: ResourceState) -> tuple[AnalyzeDiscovery, ...]:
    return tuple(
        AnalyzeDiscovery(
            actor_id=r.actor_id,
            item_id=r.item_id,
            binding_id=r.binding_id,
            project_id=r.project_id,
            power=r.actual_power,
        )
        for r in reports(state)
        if r.truthful
    )


def knows_power(state: ResourceState, actor_id: str, binding: MagicItemInstance) -> bool:
    return any(
        (d.actor_id, d.item_id, d.binding_id, d.project_id, d.power)
        == (actor_id, binding.item_id, binding.id, binding.project_id, binding.power)
        for d in discovery_records(state)
    )


def projection(state: ResourceState, actor_ids: tuple[str, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "actor_id": r.actor_id,
            "item_id": r.item_id,
            "spell_id": r.spell_id,
            "power": r.claimed_power,
        }
        for r in reports(state)
        if r.actor_id in actor_ids
    )


def needs_clock_checkpoints(state: ResourceState) -> bool:
    return bool(pending_actor_ids(state))


def interrupt_casts(
    state: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    for cast in casts(state).values():
        if cast.actor_id == actor_id and cast.status in ("casting", "ready"):
            updated = cast.model_copy(
                update={"distracted": True} if distraction else {"status": "cancelled"}
            )
            state = append(state, "cast", command_id + ":" + cast.cast_id, actor_id, updated)
    return state


def apparent_power(state: ResourceState, actor_id: str, binding: MagicItemInstance) -> int | None:
    return next(
        (
            r.claimed_power
            for r in reversed(reports(state))
            if (r.actor_id, r.item_id, r.binding_id, r.project_id, r.actual_power)
            == (actor_id, binding.item_id, binding.id, binding.project_id, binding.power)
        ),
        None,
    )
