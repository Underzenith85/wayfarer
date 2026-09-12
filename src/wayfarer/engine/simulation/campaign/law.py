"""Jurisdiction, legality and enforcement procedures (Basic Set B506-B509)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import Contestant, quick_contest, success_roll
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, reaction_roll
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class Jurisdiction(Record):
    id: Id
    control_rating: int = Field(ge=0, le=6)


class LegalityRule(Record):
    definition_id: Id
    legality_class: int = Field(ge=0, le=4)


class PermitRule(Record):
    id: Id
    jurisdiction_id: Id
    definition_id: Id
    holder_id: Id
    scope: Literal["carry", "own", "official"] = "carry"


class CrimeRule(Record):
    id: Id
    jurisdiction_id: Id
    offense: str = Field(min_length=1, max_length=1000)
    evidence_fact_ids: tuple[Id, ...] = ()


class EnforcementProcedure(Record):
    id: Id
    jurisdiction_id: Id
    kind: Literal["etiquette", "encounter", "arrest", "jail", "trial", "bribery", "punishment"]
    from_status: Literal["accused", "encounter", "arrested", "jailed", "trial", "convicted"]
    success_status: Literal[
        "encounter", "arrested", "jailed", "trial", "convicted", "acquitted", "punished", "released"
    ]
    failure_status: Literal[
        "encounter", "arrested", "jailed", "trial", "convicted", "acquitted", "punished", "released"
    ]
    resolution: Literal["automatic", "success", "reaction", "contest"] = "automatic"
    primary_target: int = 10
    opposing_target: int | None = None
    duration: int = Field(default=0, ge=0)
    reaction_modifiers: tuple[ReactionModifier, ...] = ()
    consequence: str = Field(default="", max_length=1000)


class LawRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    jurisdictions: tuple[Jurisdiction, ...] = Field(min_length=1)
    legality: tuple[LegalityRule, ...] = ()
    permits: tuple[PermitRule, ...] = ()
    crimes: tuple[CrimeRule, ...] = ()
    procedures: tuple[EnforcementProcedure, ...] = ()

    @model_validator(mode="after")
    def valid_rules(self) -> LawRules:
        jurisdiction_ids = {item.id for item in self.jurisdictions}
        for keys, label in (
            (tuple(item.id for item in self.jurisdictions), "jurisdiction"),
            (tuple(item.definition_id for item in self.legality), "legality definition"),
            (tuple(item.id for item in self.permits), "permit"),
            (tuple(item.id for item in self.crimes), "crime"),
            (tuple(item.id for item in self.procedures), "procedure"),
        ):
            if len(set(keys)) != len(keys):
                raise ValueError(f"Duplicate law {label}")
        referenced = (
            tuple(item.jurisdiction_id for item in self.permits)
            + tuple(item.jurisdiction_id for item in self.crimes)
            + tuple(item.jurisdiction_id for item in self.procedures)
        )
        if not set(referenced) <= jurisdiction_ids:
            raise ValueError("Law rule references an unknown jurisdiction")
        return self


class Permit(Record):
    id: Id
    rule_id: Id
    issued_at: int = Field(ge=0)


class EvidenceRecord(Record):
    fact_id: Id
    authority_ids: tuple[Id, ...] = Field(min_length=1)


class LawCase(Record):
    id: Id
    crime_id: Id
    subject_id: Id
    jurisdiction_id: Id
    status: Literal[
        "accused",
        "encounter",
        "arrested",
        "jailed",
        "trial",
        "convicted",
        "acquitted",
        "punished",
        "released",
    ] = "accused"
    evidence: tuple[EvidenceRecord, ...] = ()
    procedures: tuple[Id, ...] = ()
    consequence: str = ""


class LawState(Record):
    permits: tuple[Permit, ...] = ()
    cases: tuple[LawCase, ...] = ()

    def evidence_for(self, authority_id: str) -> tuple[tuple[str, str], ...]:
        return tuple(
            (case.id, evidence.fact_id)
            for case in self.cases
            for evidence in case.evidence
            if authority_id in evidence.authority_ids
        )


class IssuePermit(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    kind: Literal["permit"] = "permit"
    permit_rule_id: Id


class RecordCrime(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    kind: Literal["crime"] = "crime"
    crime_rule_id: Id
    subject_id: Id
    authority_ids: tuple[Id, ...] = Field(min_length=1)


class ResolveLawCase(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    kind: Literal["procedure"] = "procedure"
    case_id: Id
    procedure_id: Id


LawCommand = Annotated[IssuePermit | RecordCrime | ResolveLawCase, Field(discriminator="kind")]
LAW_COMMAND_ADAPTER: TypeAdapter[LawCommand] = TypeAdapter(LawCommand)


class LawOutcome(Record):
    status: str
    availability: (
        Literal["open", "registered", "licensed", "restricted", "prohibited", "permitted"] | None
    ) = None
    private: str = ""
    consequence: str = ""


def availability(
    rules: LawRules,
    state: LawState,
    *,
    jurisdiction_id: str,
    definition_id: str,
    actor_id: str,
) -> LawOutcome:
    jurisdiction = next((item for item in rules.jurisdictions if item.id == jurisdiction_id), None)
    item = next((item for item in rules.legality if item.definition_id == definition_id), None)
    if jurisdiction is None or item is None:
        raise ValidationError("Legal availability requires authored jurisdiction and item ratings")
    permit_ids = {permit.rule_id for permit in state.permits}
    if any(
        rule.id in permit_ids
        and rule.jurisdiction_id == jurisdiction_id
        and rule.definition_id == definition_id
        and rule.holder_id == actor_id
        for rule in rules.permits
    ):
        return LawOutcome(status="available", availability="permitted")
    difference = item.legality_class - jurisdiction.control_rating
    category: Literal["open", "registered", "licensed", "restricted", "prohibited"] = (
        "open"
        if difference >= 1
        else "registered"
        if difference == 0
        else "licensed"
        if difference == -1
        else "restricted"
        if difference == -2
        else "prohibited"
    )
    return LawOutcome(status="available", availability=category)


def _digest(command: LawCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _finish(
    resources: ResourceState, command: LawCommand, outcome: LawOutcome, revision: int
) -> ResourceState:
    return resources.model_copy(
        update={
            "revision": revision,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="law:" + command.id,
                    at=resources.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )


def _procedure_check(
    command: ResolveLawCase,
    case: LawCase,
    procedure: EnforcementProcedure,
    rules: LawRules,
    rng: RandomSource,
) -> tuple[bool, str]:
    if procedure.resolution == "automatic":
        return True, ""
    if procedure.resolution == "success":
        check_trace = success_roll(rules.profile_id, procedure.primary_target, rng=rng)
        return check_trace.outcome.succeeded, json.dumps(asdict(check_trace), default=str)
    if procedure.resolution == "reaction":
        reaction_trace = reaction_roll(rules.profile_id, procedure.reaction_modifiers, rng=rng)
        passed = reaction_trace.outcome in ("neutral", "good", "very-good", "excellent")
        return passed, json.dumps(asdict(reaction_trace), default=str)
    if procedure.opposing_target is None:
        raise ValidationError("Adversarial trial requires both authored targets")
    contest_trace = quick_contest(
        rules.profile_id,
        Contestant(command.actor_id, procedure.primary_target),
        Contestant("authority:" + case.jurisdiction_id, procedure.opposing_target),
        rng=rng,
    )
    return contest_trace.winner == command.actor_id, json.dumps(asdict(contest_trace), default=str)


def _procedure(
    state: LawState,
    resources: ResourceState,
    command: ResolveLawCase,
    rules: LawRules,
    rng: RandomSource,
    advance: Callable[[ResourceState, int, str], ResourceState] | None,
) -> tuple[LawState, ResourceState, LawOutcome]:
    case = next((item for item in state.cases if item.id == command.case_id), None)
    procedure = next((item for item in rules.procedures if item.id == command.procedure_id), None)
    if case is None or procedure is None or procedure.jurisdiction_id != case.jurisdiction_id:
        raise ValidationError("Law case procedure is not authored for this jurisdiction")
    if case.status != procedure.from_status or procedure.id in case.procedures:
        raise ConflictError("Law procedure is not available in the case's current state")
    succeeded, private = _procedure_check(command, case, procedure, rules, rng)
    if procedure.duration:
        if advance is None:
            raise ValidationError("Timed law procedure requires the shared clock reducer")
        resources = advance(resources, resources.game_time + procedure.duration, command.id)
    status = procedure.success_status if succeeded else procedure.failure_status
    case = case.model_copy(
        update={
            "status": status,
            "procedures": case.procedures + (procedure.id,),
            "consequence": procedure.consequence if status == "punished" else case.consequence,
        }
    )
    state = state.model_copy(
        update={"cases": tuple(case if item.id == case.id else item for item in state.cases)}
    )
    return (
        state,
        resources,
        LawOutcome(status=status, private=private, consequence=case.consequence),
    )


def apply_law(
    state: LawState,
    resources: ResourceState,
    command: LawCommand,
    rules: LawRules,
    *,
    rng: RandomSource,
    known_fact_ids: frozenset[str],
    advance: Callable[[ResourceState, int, str], ResourceState] | None = None,
    system: bool = False,
) -> tuple[LawState, ResourceState, LawOutcome]:
    if not system:
        raise ValidationError("Law procedures require engine authority")
    prior = next((item for item in resources.receipts if item.command_id == command.id), None)
    if prior is not None:
        if prior.digest != _digest(command):
            raise ConflictError("Law command ID reused")
        event = next(item for item in resources.events if item.id == "law:" + command.id)
        return state, resources, LawOutcome.model_validate_json(event.kind)
    if command.expected_revision != resources.revision:
        raise ConflictError("Law procedure revision changed")
    revision = resources.revision + 1
    if isinstance(command, IssuePermit):
        permit_rule = next(
            (item for item in rules.permits if item.id == command.permit_rule_id), None
        )
        if permit_rule is None:
            raise ValidationError("Permit was not authored by this campaign")
        state = state.model_copy(
            update={
                "permits": state.permits
                + (
                    Permit(
                        id=command.id,
                        rule_id=permit_rule.id,
                        issued_at=resources.game_time,
                    ),
                )
            }
        )
        outcome = LawOutcome(status="permit-issued")
    elif isinstance(command, RecordCrime):
        crime_rule = next((item for item in rules.crimes if item.id == command.crime_rule_id), None)
        if crime_rule is None:
            raise ValidationError("Crime was not authored by this campaign")
        if not set(crime_rule.evidence_fact_ids) <= known_fact_ids:
            raise ValidationError("A crime cannot create evidence that is not world truth")
        case = LawCase(
            id=command.id,
            crime_id=crime_rule.id,
            subject_id=command.subject_id,
            jurisdiction_id=crime_rule.jurisdiction_id,
            evidence=tuple(
                EvidenceRecord(fact_id=fact, authority_ids=command.authority_ids)
                for fact in crime_rule.evidence_fact_ids
            ),
        )
        state = state.model_copy(update={"cases": state.cases + (case,)})
        outcome = LawOutcome(status="accused")
    else:
        state, resources, outcome = _procedure(state, resources, command, rules, rng, advance)
    resources = _finish(resources, command, outcome, revision)
    return state, resources, outcome


def validate_law(rules: LawRules | None, state: LawState, fact_ids: frozenset[str]) -> None:
    if rules is None:
        if state != LawState():
            raise ValidationError("Law state requires authored rules")
        return
    if len({item.id for item in state.permits}) != len(state.permits):
        raise ValidationError("Duplicate issued permit")
    if len({item.id for item in state.cases}) != len(state.cases):
        raise ValidationError("Duplicate law case")
    if not {item.rule_id for item in state.permits} <= {item.id for item in rules.permits}:
        raise ValidationError("Issued permit lacks an authored rule")
    crimes = {item.id: item for item in rules.crimes}
    procedures = {item.id for item in rules.procedures}
    for case in state.cases:
        crime = crimes.get(case.crime_id)
        if crime is None or crime.jurisdiction_id != case.jurisdiction_id:
            raise ValidationError("Law case lacks an authored crime")
        if not set(case.procedures) <= procedures:
            raise ValidationError("Law case references an unknown procedure")
        if any(item.fact_id not in fact_ids for item in case.evidence):
            raise ValidationError("Law case evidence is not world truth")
