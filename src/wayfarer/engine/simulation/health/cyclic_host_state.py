"""Private source-authored Cyclic policy and immutable occurrence bindings.

These records live in the private resource event ledger, not authoring or public
command schemas. A policy is approved before delivery; an observation cannot
supply replacement damage, resistance, HT, bonuses or a stopping condition.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.disease import ContactKind
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

BINDING_PREFIX = "cyclic-host-binding:"
HISTORY_PREFIX = "cyclic-host:"


class CyclicProcedure(Record):
    seconds: int = Field(ge=1)
    check: Literal["dx", "iq", "physician"] | None = None
    consume_definition_id: str | None = None
    quantity: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.consume_definition_id is None and self.quantity != 1:
            raise ValueError("A quantity requires a procedure resource")
        return self


class CyclicPrecaution(Record):
    """B443 GM-assigned precaution; the existing ContactExposure boundary caps at 10."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    bonus: int = Field(ge=0, le=10)


class CyclicPolicy(Record):
    condition: str = Field(min_length=1)
    procedure: CyclicProcedure | None = None
    precautions: tuple[CyclicPrecaution, ...] = ()
    infectious_during_incubation: bool = False

    @model_validator(mode="after")
    def valid(self) -> Self:
        if len({p.id for p in self.precautions}) != len(self.precautions):
            raise ValueError("Cyclic precaution identities must be unique")
        return self


class CyclicBinding(Record):
    occurrence_id: str
    source_id: str
    source_revision: str
    actor_id: str
    policy: CyclicPolicy
    bypass_dr: bool = False
    disease: bool = False
    location: HumanLocation | None = Field(default=None, exclude_if=lambda value: value is None)


def binding(state: ResourceState, occurrence_id: str) -> CyclicBinding | None:
    values = tuple(e for e in state.events if e.id == BINDING_PREFIX + occurrence_id)
    if len(values) > 1:
        raise ConflictError("Ambiguous Cyclic occurrence binding")
    return CyclicBinding.model_validate_json(values[0].kind) if values else None


def bind_occurrence(
    state: ResourceState,
    occurrence_id: str,
    *,
    source_id: str,
    source_revision: str,
    policy: CyclicPolicy,
    bypass_dr: bool = False,
    disease: bool = False,
    location: HumanLocation | None = None,
) -> ResourceState:
    attack = next((a for a in state.cyclic_attacks if a.id == occurrence_id), None)
    if attack is None or attack.attack_id != source_id or attack.stop_condition != policy.condition:
        raise ValidationError("Cyclic binding must match its approved source and stopping policy")
    value = CyclicBinding(
        occurrence_id=occurrence_id,
        source_id=source_id,
        source_revision=source_revision,
        actor_id=attack.actor_id,
        policy=policy,
        bypass_dr=bypass_dr,
        disease=disease,
        location=None if disease else location,
    )
    prior = binding(state, occurrence_id)
    if prior is not None:
        if prior != value:
            raise ConflictError("Cyclic occurrence is already bound to another source policy")
        return state
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=BINDING_PREFIX + occurrence_id,
                    at=state.game_time,
                    target_id=attack.actor_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


class ObserveCyclicStop(Command):
    kind: Literal["observe-cyclic-stop"] = "observe-cyclic-stop"
    occurrence_id: str
    location_id: str
    reason: str = Field(min_length=1, max_length=2000)


class AdjudicateCyclicLocationLoss(Command):
    """GM campaign policy for an occurrence whose bound limb no longer applies."""

    kind: Literal["adjudicate-cyclic-location-loss"] = "adjudicate-cyclic-location-loss"
    occurrence_id: str
    location_id: str
    campaign_policy: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class CyclicLocationAdjudication(Record):
    authority: Literal["gm-campaign-adjudication"] = "gm-campaign-adjudication"
    campaign_policy: str
    source_id: str
    source_revision: str
    location: HumanLocation
    anatomy: Literal["human", "creature", "swarm"]
    evidence: Literal["severed-location", "incompatible-body"]
    injury_ids: tuple[str, ...] = ()


class BeginCyclicProcedure(Command):
    kind: Literal["begin-cyclic-procedure"] = "begin-cyclic-procedure"
    occurrence_id: str
    performer_id: str
    location_id: str
    item_id: str | None = None
    reason: str = Field(min_length=1, max_length=2000)


class CompleteCyclicProcedure(Command):
    kind: Literal["complete-cyclic-procedure"] = "complete-cyclic-procedure"
    occurrence_id: str
    procedure_id: str
    location_id: str
    uninterrupted: Literal[True]
    reason: str = Field(min_length=1, max_length=2000)


class ObserveCyclicExposure(Command):
    kind: Literal["observe-cyclic-exposure"] = "observe-cyclic-exposure"
    source_occurrence_id: str
    location_id: str
    contact: ContactKind
    # Qualitative vector facts are explicit, separate from mere co-location.
    route: Literal["blood-entry", "skin-contact", "ingestion", "shared-air"]
    precaution_id: str | None = None
    precaution_understood: bool = False
    early_incubation_resolution: Literal["defer-to-daily-check"] | None = None
    reason: str = Field(min_length=1, max_length=2000)


CyclicHostCommand = (
    ObserveCyclicStop
    | AdjudicateCyclicLocationLoss
    | BeginCyclicProcedure
    | CompleteCyclicProcedure
    | ObserveCyclicExposure
)
ADAPTER: TypeAdapter[CyclicHostCommand] = TypeAdapter(
    Annotated[CyclicHostCommand, Field(discriminator="kind")]
)


class CyclicHostReceipt(Record):
    command_id: str
    kind: Literal["stopped", "retired", "procedure-started", "procedure-failed", "exposed"]
    occurrence_id: str
    actor_id: str
    principal_id: str
    at: int
    location_id: str
    reason: str
    performer_id: str | None = None
    item_id: str | None = None
    procedure_id: str | None = None
    check: CheckTrace | None = None
    exposure_id: str | None = None
    contact: ContactKind | None = None
    route: str | None = None
    precaution_id: str | None = None
    precaution_understood: bool = False
    early_incubation_resolution: Literal["defer-to-daily-check"] | None = None
    adjudication: CyclicLocationAdjudication | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def history(state: ResourceState) -> tuple[CyclicHostReceipt, ...]:
    return tuple(
        CyclicHostReceipt.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith(HISTORY_PREFIX)
    )


def save_receipt(state: ResourceState, result: CyclicHostReceipt) -> ResourceState:
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=HISTORY_PREFIX + result.command_id,
                    at=result.at,
                    target_id=result.actor_id,
                    kind=result.model_dump_json(),
                ),
            )
        }
    )


def require_context(state: ResourceState, to: int) -> None:
    """A bare resource clock may not leave a new host occurrence unsettled."""
    if any(
        a.active and a.due <= to and binding(state, a.id) is not None for a in state.cyclic_attacks
    ) or any(
        e.stage == "exposure" and e.due <= to and binding(state, e.source.id) is not None
        for e in state.cyclic_exposures
    ):
        raise ConflictError("Host-bound Cyclic deadlines require current approved target context")
