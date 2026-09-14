"""Authored B127/B153/B163 obligations without invented punishments.

These disadvantages constrain roleplaying.  The GM may record what happened in
one campaign situation, but the Basic Set does not supply a compulsory roll or
a fixed mechanical penalty.  Careful additionally records the preparation time
and expense actually authored for that situation.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Protocol

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PROFILE: Final[Literal["gurps-basic-set-4e-2004"]] = "gurps-basic-set-4e-2004"
OBLIGATION_HOOK: Final = "trait.authored_obligation"
ObligationKind = Literal["careful", "soldier-code", "sense-of-duty"]
DutyScope = Literal["individual", "small-group", "large-group", "race", "all-living"]
ObligationOutcome = Literal["fulfilled", "breached", "not-applicable"]
ObligationClause = Literal[
    "prepare-before-danger",
    "lead-from-front",
    "care-for-comrades",
    "maintain-kit",
    "fight-for-unit",
    "obey-orders",
    "obey-rules-of-war",
    "respect-honorable-enemy",
    "uniform-pride",
    "never-betray",
    "never-abandon",
    "prevent-suffering",
    "prevent-hunger",
    "share-equipment",
    "render-aid",
    "honor-majority-decision",
]
CLAUSES: Final = {
    "careful": frozenset({"prepare-before-danger"}),
    "soldier-code": frozenset(
        {
            "lead-from-front",
            "care-for-comrades",
            "maintain-kit",
            "fight-for-unit",
            "obey-orders",
            "obey-rules-of-war",
            "respect-honorable-enemy",
            "uniform-pride",
        }
    ),
    "sense-of-duty": frozenset(
        {
            "never-betray",
            "never-abandon",
            "prevent-suffering",
            "prevent-hunger",
            "share-equipment",
            "render-aid",
            "honor-majority-decision",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ObligationBinding:
    kind: ObligationKind
    scope: DutyScope | None = None


OBLIGATION_BINDINGS: Final = MappingProxyType(
    {
        "trait:quirk-careful": ObligationBinding("careful"),
        "trait:code-of-honor-soldier": ObligationBinding("soldier-code"),
        **{
            f"trait:sense-of-duty-{scope}": ObligationBinding("sense-of-duty", scope)
            for scope in (
                "individual",
                "small-group",
                "large-group",
                "race",
                "all-living",
            )
        },
    }
)


class TraitPurchase(Protocol):
    definition_id: str


class ApprovedBuild(Protocol):
    trait_purchases: tuple[TraitPurchase, ...]


class ObligationCommand(Record):
    id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    definition_id: str = Field(min_length=1, max_length=200)
    situation_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)
    clause: ObligationClause
    outcome: ObligationOutcome
    beneficiary_ids: tuple[str, ...] = Field(default=(), max_length=100)
    preparation_time: int = Field(default=0, ge=0, le=1_000_000)
    preparation_expense: int = Field(default=0, ge=0, le=1_000_000_000)
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def source_bounded_facts(self) -> ObligationCommand:
        binding = OBLIGATION_BINDINGS.get(self.definition_id)
        if binding is None:
            raise ValueError("Trait has no authored obligation binding")
        if self.clause not in CLAUSES[binding.kind]:
            raise ValueError("Obligation clause does not belong to this trait")
        if len(set(self.beneficiary_ids)) != len(self.beneficiary_ids):
            raise ValueError("Obligation beneficiaries must be unique")
        if binding.kind == "sense-of-duty" and not self.beneficiary_ids:
            raise ValueError("Sense of Duty requires an authored beneficiary")
        if self.clause in {"share-equipment", "honor-majority-decision"} and binding.scope != (
            "small-group"
        ):
            raise ValueError("Adventuring-companion duties require the small-group construction")
        if binding.kind == "careful" and self.beneficiary_ids:
            raise ValueError("Careful does not record beneficiaries")
        preparation = self.preparation_time or self.preparation_expense
        if binding.kind == "careful" and self.outcome == "fulfilled" and not preparation:
            raise ValueError("A fulfilled Careful obligation records authored preparation")
        if binding.kind != "careful" and preparation:
            raise ValueError("Only Careful records preparation time or expense")
        if self.outcome != "fulfilled" and preparation:
            raise ValueError("Preparation applies only to a fulfilled Careful obligation")
        return self


class ObligationReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    command: ObligationCommand
    authority_id: str = Field(min_length=1, max_length=200)
    kind: ObligationKind
    scope: DutyScope | None = None
    compulsory_roll: Literal[False] = False
    point_change: Literal[0] = 0
    mechanical_penalty: None = None


class ObligationState(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    revision: int = Field(default=0, ge=0)
    receipts: tuple[ObligationReceipt, ...] = ()

    @model_validator(mode="after")
    def exact_history(self) -> ObligationState:
        ids = tuple(receipt.command.id for receipt in self.receipts)
        if len(ids) != len(set(ids)):
            raise ValueError("Obligation history contains duplicate command IDs")
        return self


def record_obligation(
    state: ObligationState,
    build: ApprovedBuild,
    command: ObligationCommand,
    *,
    principal_id: str,
    gm_ids: frozenset[str],
) -> tuple[ObligationState, ObligationReceipt]:
    """Record one GM judgment with deterministic CAS and retry semantics."""
    prior = next((receipt for receipt in state.receipts if receipt.command.id == command.id), None)
    if prior is not None:
        if prior.command != command or prior.authority_id != principal_id:
            raise ConflictError("Obligation command ID was already used for different facts")
        return state, prior
    if command.expected_revision != state.revision:
        raise ConflictError("Obligation state changed")
    if principal_id not in gm_ids:
        raise ValidationError("Obligation judgment requires campaign GM authority")
    if command.definition_id not in {purchase.definition_id for purchase in build.trait_purchases}:
        raise ValidationError("Actor does not have the approved obligation trait")
    binding = OBLIGATION_BINDINGS[command.definition_id]
    receipt = ObligationReceipt(
        command=command,
        authority_id=principal_id,
        kind=binding.kind,
        scope=binding.scope,
    )
    return (
        state.model_copy(
            update={"revision": state.revision + 1, "receipts": state.receipts + (receipt,)}
        ),
        receipt,
    )
