"""Executable associated-NPC outcomes from Characters B36/B44/B72/B131/B135."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, cast

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource, draw_dice
from wayfarer.engine.rules.conformance_vocabulary import BASIC_PROFILE_ID
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.traits.mental import Relationship, RelationshipKind, frequency_roll
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

ASSOCIATED_NPC_HOOK: Final = "trait.associated_npc.bound"
CONTACT_HOOK: Final = "trait.contact.bound"
RELATIONSHIP_HOOKS: Final = frozenset({ASSOCIATED_NPC_HOOK, CONTACT_HOOK})
RelationshipEvent = Literal[
    "ordinary",
    "betrayal",
    "severe-betrayal",
    "death-no-fault",
    "amicable-parting",
    "aid",
    "assignment",
    "harmed",
    "lost",
    "eliminated",
]
RelationshipOutcome = Literal[
    "accompanies",
    "bonus-forfeited",
    "relationship-broken",
    "replacement-permitted",
    "parted",
    "information",
    "delayed",
    "lies",
    "unreachable",
    "unavailable-day",
    "unavailable-adventure",
    "patron-aid",
    "patron-assignment",
    "omitted",
    "causes-trouble",
    "dependent-harmed",
    "dependent-lost",
    "opposes",
    "enemy-eliminated",
]
RelationshipSettlement = Literal[
    "none",
    "purchased-points-lost",
    "replacement-permitted",
    "buy-off-or-replace-disadvantage",
]


@dataclass(frozen=True, slots=True)
class RelationshipBinding:
    kind: RelationshipKind
    person_id: str
    hook: str
    frequency: int
    character_points_percent: int | None = None
    contact_skill: int | None = None
    reliability: str = "usually-reliable"
    patron_power: str | None = None


RELATIONSHIP_BINDINGS: Final = MappingProxyType(
    {
        "trait:ally-associate": RelationshipBinding(
            "ally", "associate", ASSOCIATED_NPC_HOOK, 9, character_points_percent=100
        ),
        "trait:contact-associate": RelationshipBinding(
            "contact", "associate", CONTACT_HOOK, 9, contact_skill=12
        ),
        "trait:patron-associate": RelationshipBinding(
            "patron", "associate", ASSOCIATED_NPC_HOOK, 9, patron_power="wealthy"
        ),
        "trait:dependent-associate": RelationshipBinding(
            "dependent", "associate", ASSOCIATED_NPC_HOOK, 9, character_points_percent=50
        ),
        "trait:enemy-associate": RelationshipBinding("enemy", "associate", ASSOCIATED_NPC_HOOK, 9),
    }
)


def relationship_definition_id(relationship: Relationship) -> str:
    return f"trait:{relationship.kind}-{relationship.person_id}"


def validate_relationship_binding(definition_id: str, relationship: Relationship) -> None:
    binding = RELATIONSHIP_BINDINGS.get(definition_id)
    if binding is None or definition_id != relationship_definition_id(relationship):
        raise ValidationError("Relationship has no exact purchased trait binding")
    actual = (
        relationship.kind,
        relationship.person_id,
        relationship.frequency,
        relationship.character_points_percent,
        relationship.contact_skill,
        relationship.reliability,
        relationship.patron_power,
    )
    expected = (
        binding.kind,
        binding.person_id,
        binding.frequency,
        binding.character_points_percent,
        binding.contact_skill,
        binding.reliability,
        binding.patron_power,
    )
    if actual != expected:
        raise ValidationError("Relationship facts differ from the purchased construction")


class RelationshipContext(Record):
    actor_id: str = Field(min_length=1, max_length=200)
    adventure_id: str = Field(min_length=1, max_length=200)
    day: int = Field(ge=0)
    relationship: Relationship
    known_npc_ids: frozenset[str] = Field(min_length=1, max_length=1000)
    channels_open: bool = True
    patron_applicable: bool = True
    event: RelationshipEvent = "ordinary"

    @model_validator(mode="after")
    def authoritative_scene(self) -> RelationshipContext:
        if self.relationship.person_id not in self.known_npc_ids:
            raise ValueError("Relationship person is not an authoritative campaign NPC")
        kind = self.relationship.kind
        allowed = {
            "ally": {
                "ordinary",
                "betrayal",
                "severe-betrayal",
                "death-no-fault",
                "amicable-parting",
            },
            "contact": {"ordinary"},
            "patron": {"aid", "assignment"},
            "dependent": {"ordinary", "harmed", "lost"},
            "enemy": {"ordinary", "eliminated"},
        }[kind]
        if self.event not in allowed:
            raise ValueError("Authored relationship event does not belong to this trait")
        if kind != "contact" and not self.channels_open:
            raise ValueError("Communication channels apply only to Contacts")
        if kind != "patron" and not self.patron_applicable:
            raise ValueError("Patron applicability applies only to Patrons")
        return self


class RelationshipCommand(Record):
    id: str = Field(min_length=1, max_length=200)
    actor_id: str = Field(min_length=1, max_length=200)
    definition_id: str = Field(min_length=1, max_length=200)
    relationship_id: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0)


class RelationshipReceipt(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = BASIC_PROFILE_ID
    command: RelationshipCommand
    context: RelationshipContext
    kind: RelationshipKind
    appearance_dice: tuple[int, int, int] | None = None
    appears: bool
    outcome: RelationshipOutcome
    contact_check: CheckTrace | None = None
    delay_days: int = Field(default=0, ge=0, le=6)
    bonus_points_forfeited: bool = False
    relationship_ends: bool = False
    settlement: RelationshipSettlement = "none"


class RelationshipState(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = BASIC_PROFILE_ID
    revision: int = Field(default=0, ge=0)
    receipts: tuple[RelationshipReceipt, ...] = ()

    @model_validator(mode="after")
    def unique_commands(self) -> RelationshipState:
        ids = tuple(receipt.command.id for receipt in self.receipts)
        if len(ids) != len(set(ids)):
            raise ValueError("Relationship history contains duplicate command IDs")
        return self


def _contact_outcome(
    relationship: Relationship, rng: RandomSource
) -> tuple[RelationshipOutcome, CheckTrace, int]:
    assert relationship.contact_skill is not None
    check = success_roll(BASIC_PROFILE_ID, relationship.contact_skill, rng=rng)
    if check.outcome.succeeded:
        return "information", check, 0
    if check.outcome is Outcome.CRITICAL_FAILURE:
        return "lies", check, 0
    delay = draw_dice(rng, 1)[0]
    return "delayed", check, delay


def _appearing_outcome(
    context: RelationshipContext,
) -> tuple[RelationshipOutcome, bool, bool, RelationshipSettlement]:
    value = {
        "ally": {
            "ordinary": ("accompanies", False, False, "none"),
            "betrayal": ("bonus-forfeited", True, False, "none"),
            "severe-betrayal": (
                "relationship-broken",
                True,
                True,
                "purchased-points-lost",
            ),
            "death-no-fault": (
                "replacement-permitted",
                False,
                True,
                "replacement-permitted",
            ),
            "amicable-parting": ("parted", False, True, "replacement-permitted"),
        },
        "patron": {
            "aid": ("patron-aid", False, False, "none"),
            "assignment": ("patron-assignment", False, False, "none"),
        },
        "dependent": {
            "ordinary": ("causes-trouble", False, False, "none"),
            "harmed": ("dependent-harmed", False, False, "none"),
            "lost": (
                "dependent-lost",
                False,
                True,
                "buy-off-or-replace-disadvantage",
            ),
        },
        "enemy": {
            "ordinary": ("opposes", False, False, "none"),
            "eliminated": (
                "enemy-eliminated",
                False,
                True,
                "buy-off-or-replace-disadvantage",
            ),
        },
    }[context.relationship.kind][context.event]
    return cast(tuple[RelationshipOutcome, bool, bool, RelationshipSettlement], value)


def resolve_relationship(
    state: RelationshipState,
    command: RelationshipCommand,
    context: RelationshipContext,
    *,
    rng: RandomSource,
) -> tuple[RelationshipState, RelationshipReceipt]:
    """Resolve one relationship occurrence and retain every consumed die."""
    prior = next((receipt for receipt in state.receipts if receipt.command.id == command.id), None)
    if prior is not None:
        if prior.command != command or prior.context != context:
            raise ConflictError("Relationship command ID was already used for different facts")
        return state, prior
    if command.expected_revision != state.revision:
        raise ConflictError("Relationship state changed")
    if command.actor_id != context.actor_id:
        raise ValidationError("Relationship actor differs from authoritative character context")
    if command.relationship_id != context.relationship.id:
        raise ValidationError("Relationship command does not name the persisted relationship")
    validate_relationship_binding(command.definition_id, context.relationship)
    same_period = next(
        (
            receipt
            for receipt in state.receipts
            if receipt.command.relationship_id == command.relationship_id
            and receipt.context.adventure_id == context.adventure_id
            and (context.relationship.kind != "contact" or receipt.context.day == context.day)
        ),
        None,
    )
    if same_period is not None:
        raise ConflictError("Relationship occurrence was already resolved for this period")

    relationship = context.relationship
    outcome: RelationshipOutcome
    if relationship.kind == "contact" and not context.channels_open:
        appearance_dice, appears, outcome = None, False, "unreachable"
        contact_check, delay = None, 0
    elif relationship.kind == "patron" and not context.patron_applicable:
        appearance_dice, appears, outcome = None, False, "omitted"
        contact_check, delay = None, 0
    else:
        appearance = frequency_roll(relationship, rng)
        appearance_dice, appears = appearance.dice, appearance.appears
        contact_check, delay = None, 0
        if not appears:
            outcome = (
                "unavailable-adventure"
                if relationship.kind != "contact"
                or appearance_dice is not None
                and sum(appearance_dice) >= 17
                else "unavailable-day"
            )
        elif relationship.kind == "contact":
            outcome, contact_check, delay = _contact_outcome(relationship, rng)
        else:
            outcome, forfeited, ends, settlement = _appearing_outcome(context)
    if relationship.kind == "contact" or not appears:
        forfeited, ends, settlement = False, False, "none"
    receipt = RelationshipReceipt(
        command=command,
        context=context,
        kind=relationship.kind,
        appearance_dice=appearance_dice,
        appears=appears,
        outcome=outcome,
        contact_check=contact_check,
        delay_days=delay,
        bonus_points_forfeited=forfeited,
        relationship_ends=ends,
        settlement=settlement,
    )
    return (
        state.model_copy(
            update={"revision": state.revision + 1, "receipts": state.receipts + (receipt,)}
        ),
        receipt,
    )
