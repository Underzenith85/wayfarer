"""Authoritative Basic Set character creation around the existing compiler.

The ordinary :class:`CharacterCompiler` remains the sole owner of catalog
purchases and their prices.  This module compiles the B10-B31 construction
facts that do not belong in a purchase payload: physical description,
campaign background, associated NPCs, identities, and starting equipment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic import ValidationError as SchemaError

from wayfarer.engine.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Diagnostic,
    ValidatedBuild,
)
from wayfarer.engine.character.traits.background import BackgroundContext, background_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.traits.background import LEVELS, BackgroundTraits
from wayfarer.engine.rules.traits.mental import (
    Relationship,
    relationship_cost,
    validate_relationships,
)
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PhysicalDescription(Record):
    """Rule-relevant physical facts; free-form description grants no mechanics."""

    height_inches: int | None = Field(default=None, ge=1, le=10_000)
    weight_pounds: int | None = Field(default=None, ge=1, le=10_000_000)
    build: Literal["skinny", "average", "overweight", "fat", "very-fat"] = "average"
    size_modifier: int = Field(default=0, ge=-10, le=10)
    age_years: int | None = Field(default=None, ge=0, le=10_000)
    description: str = Field(default="", max_length=2_000)


class Identity(Record):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["legal", "alternate", "secret"]
    known_actor_ids: tuple[str, ...] = Field(default=(), strict=False, max_length=100)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.known_actor_ids)) != len(self.known_actor_ids):
            raise ValueError("Duplicate identity audience")
        return self


class LanguageUse(Record):
    language_id: str = Field(min_length=1, max_length=60)
    mode: Literal["spoken", "written"]
    minimum: Literal["broken", "accented", "native"] = "broken"


class EquipmentSelection(Record):
    definition_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=1_000)


class EquipmentGrant(Record):
    id: str = Field(min_length=1, max_length=200)
    definition_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=1_000)


class ConstructionDraft(Record):
    character: CharacterDraft
    physical: PhysicalDescription = PhysicalDescription()
    personal_technology_level: int = Field(ge=0, le=12)
    native_language: str = Field(min_length=1, max_length=60)
    native_culture: str = Field(min_length=1, max_length=60)
    relationships: tuple[Relationship, ...] = Field(default=(), strict=False, max_length=100)
    identities: tuple[Identity, ...] = Field(default=(), strict=False, max_length=100)
    language_uses: tuple[LanguageUse, ...] = Field(default=(), strict=False, max_length=100)
    equipment: tuple[EquipmentSelection, ...] = Field(default=(), strict=False, max_length=1_000)


class ConstructionContext(Record):
    """Trusted campaign inputs; none of these values come from a character draft."""

    campaign_technology_level: int = Field(ge=0, le=12)
    average_starting_wealth: int = Field(ge=0)
    organization_memberships: tuple[str, ...] = Field(default=(), strict=False, max_length=100)
    equipment_prices: dict[str, int] = Field(default_factory=dict)
    equipment_grants: tuple[EquipmentGrant, ...] = Field(default=(), strict=False, max_length=100)
    require_exact_point_budget: bool = True
    minimum_adult_age: int = Field(default=18, ge=0, le=1_000)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if any(type(price) is not int or price < 0 for price in self.equipment_prices.values()):
            raise ValueError("Equipment prices must be nonnegative integers")
        if len({grant.id for grant in self.equipment_grants}) != len(self.equipment_grants):
            raise ValueError("Duplicate equipment grant")
        return self


@dataclass(frozen=True, slots=True)
class WealthTransaction:
    kind: Literal["starting-wealth", "purchase", "grant"]
    amount: int
    definition_id: str | None = None
    quantity: int = 1
    provenance_id: str | None = None


@dataclass(frozen=True, slots=True)
class StartingWealthLedger:
    opening_balance: int
    transactions: tuple[WealthTransaction, ...]
    remaining: int


@dataclass(frozen=True, slots=True)
class ConstructedCharacter:
    revision: str
    build: ValidatedBuild
    spent: int
    background: BackgroundTraits
    physical: PhysicalDescription
    relationships: tuple[Relationship, ...]
    identities: tuple[Identity, ...]
    wealth: StartingWealthLedger


@dataclass(frozen=True, slots=True)
class ConstructionCompilation:
    diagnostics: tuple[Diagnostic, ...]
    spent: int
    remaining: int
    character: ConstructedCharacter | None

    @property
    def legal(self) -> bool:
        return not self.diagnostics


def _whole_money(value: Fraction) -> int | None:
    return value.numerator if value.denominator == 1 else None


def _error(
    diagnostics: list[Diagnostic], code: str, path: tuple[str | int, ...], message: str
) -> None:
    diagnostics.append(Diagnostic(code, path, message))


def _validate_technology(
    draft: ConstructionDraft, context: ConstructionContext, diagnostics: list[Diagnostic]
) -> None:
    if draft.personal_technology_level != context.campaign_technology_level:
        _error(
            diagnostics,
            "background.personal_tl_unavailable",
            ("personal_technology_level",),
            "Personal TL differs from campaign TL without an approved TL trait",
        )
    for index, purchase in enumerate(draft.character.purchases):
        if purchase.technology_level is not None and (
            purchase.technology_level != draft.personal_technology_level
        ):
            _error(
                diagnostics,
                "background.skill_tl_mismatch",
                ("character", "purchases", index, "technology_level"),
                "A technological skill must be learned at the character's personal TL",
            )


def _validate_physical(
    draft: ConstructionDraft, context: ConstructionContext, diagnostics: list[Diagnostic]
) -> None:
    size_purchase = next(
        (p for p in draft.character.purchases if p.definition_id == "trait:size-modifier"),
        None,
    )
    purchased_size = 0 if size_purchase is None else size_purchase.amount
    if draft.physical.size_modifier != purchased_size:
        _error(
            diagnostics,
            "physical.size_modifier_mismatch",
            ("physical", "size_modifier"),
            "Rule-relevant Size Modifier must match its compiled purchase",
        )
    if (
        draft.physical.age_years is not None
        and draft.physical.age_years < context.minimum_adult_age
    ):
        _error(
            diagnostics,
            "physical.child_rules_unsupported",
            ("physical", "age_years"),
            "Child attribute and growth rules require a separate supported construction",
        )


def _compile_relationships(
    draft: ConstructionDraft,
    definitions: Mapping[str, RuleDefinition],
    diagnostics: list[Diagnostic],
) -> tuple[tuple[Relationship, ...], int]:
    try:
        relationships = validate_relationships(draft.relationships)
    except ValidationError as exc:
        relationships = draft.relationships
        _error(diagnostics, "relationship.invalid", ("relationships",), str(exc))
    points = 0
    for index, relationship in enumerate(relationships):
        definition_id = f"trait:{relationship.kind}-{relationship.person_id}"
        definition = definitions.get(definition_id)
        if definition is None or definition.point_cost is None:
            _error(
                diagnostics,
                "relationship.unavailable",
                ("relationships", index),
                "Relationship has no campaign-bound trait definition",
            )
            continue
        points += relationship_cost(relationship)
    return relationships, points


def _compile_background(
    draft: ConstructionDraft,
    build: ValidatedBuild | None,
    definitions: Mapping[str, RuleDefinition],
    context: ConstructionContext,
    diagnostics: list[Diagnostic],
) -> BackgroundTraits | None:
    if build is None:
        return None
    try:
        background = background_traits(
            build,
            definitions,
            BackgroundContext(
                native_language=draft.native_language,
                native_culture=draft.native_culture,
                organization_memberships=context.organization_memberships,
            ),
        )
    except ValidationError as exc:
        _error(diagnostics, "background.invalid", ("character", "purchases"), str(exc))
        return None
    known = {language.language_id: language for language in background.languages}
    for index, use in enumerate(draft.language_uses):
        ability = known.get(use.language_id)
        actual = "none" if ability is None else getattr(ability, use.mode)
        if LEVELS.index(actual) < LEVELS.index(use.minimum):
            _error(
                diagnostics,
                "background.language_inadequate",
                ("language_uses", index),
                "Recorded comprehension is insufficient for this language use",
            )
    return background


def _compile_wealth(
    draft: ConstructionDraft,
    background: BackgroundTraits | None,
    context: ConstructionContext,
    diagnostics: list[Diagnostic],
) -> StartingWealthLedger | None:
    if background is None:
        return None
    opening = _whole_money(background.starting_assets(context.average_starting_wealth))
    if opening is None:
        _error(
            diagnostics,
            "wealth.fractional",
            ("equipment",),
            "Starting wealth does not settle to a whole campaign currency unit",
        )
        return None
    transactions = [WealthTransaction("starting-wealth", opening)]
    remaining = opening
    granted = {grant.definition_id for grant in context.equipment_grants}
    transactions.extend(
        WealthTransaction("grant", 0, grant.definition_id, grant.quantity, grant.id)
        for grant in context.equipment_grants
    )
    for index, selection in enumerate(draft.equipment):
        if selection.definition_id in granted:
            _error(
                diagnostics,
                "wealth.duplicate_grant",
                ("equipment", index),
                "Granted equipment cannot also be purchased during creation",
            )
            continue
        price = context.equipment_prices.get(selection.definition_id)
        if price is None:
            _error(
                diagnostics,
                "wealth.equipment_unavailable",
                ("equipment", index),
                "Equipment has no trusted campaign price",
            )
            continue
        amount = price * selection.quantity
        remaining -= amount
        transactions.append(
            WealthTransaction("purchase", -amount, selection.definition_id, selection.quantity)
        )
    if remaining < 0:
        _error(
            diagnostics,
            "wealth.overspent",
            ("equipment",),
            "Starting equipment exceeds available wealth",
        )
    return StartingWealthLedger(opening, tuple(transactions), remaining)


def _validate_identities(draft: ConstructionDraft, diagnostics: list[Diagnostic]) -> None:
    ids = [identity.id for identity in draft.identities]
    legal = [identity for identity in draft.identities if identity.kind == "legal"]
    if len(ids) != len(set(ids)):
        _error(diagnostics, "identity.duplicate", ("identities",), "Identity IDs must be unique")
    if len(legal) > 1:
        _error(
            diagnostics,
            "identity.multiple_legal",
            ("identities",),
            "Only one legal identity is allowed",
        )


def _validate_balance(
    base_spent: int,
    relationship_points: int,
    point_budget: int,
    exact: bool,
    diagnostics: list[Diagnostic],
) -> tuple[int, int]:
    spent = base_spent + relationship_points
    remaining = point_budget - spent
    if spent > point_budget and not any(
        diagnostic.code == "budget.overspent" for diagnostic in diagnostics
    ):
        _error(diagnostics, "budget.overspent", ("relationships",), "Point budget exceeded")
    elif exact and remaining > 0:
        _error(
            diagnostics,
            "budget.unspent",
            ("character",),
            "Character point budget is not fully spent",
        )
    return spent, remaining


class CharacterConstructionCompiler:
    """Compile creation-only facts while delegating all purchases to one compiler."""

    def __init__(self, compiler: CharacterCompiler, context: ConstructionContext) -> None:
        if compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValueError("Authoritative construction requires the Basic Set profile")
        if (
            compiler.policy.technology_level is not None
            and compiler.policy.technology_level != context.campaign_technology_level
        ):
            raise ValueError("Construction TL disagrees with the pinned campaign policy")
        self.compiler = compiler
        self.context = context

    def compile(self, value: object, *, dry_run: bool = False) -> ConstructionCompilation:
        try:
            draft = ConstructionDraft.model_validate(value)
        except SchemaError as exc:
            return ConstructionCompilation(
                tuple(
                    Diagnostic("construction.schema", error["loc"], "Invalid construction field")
                    for error in exc.errors()
                ),
                0,
                self.compiler.policy.point_budget,
                None,
            )

        base = self.compiler.compile(draft.character)
        diagnostics = list(base.diagnostics)
        _validate_technology(draft, self.context, diagnostics)
        _validate_physical(draft, self.context, diagnostics)
        relationships, relationship_points = _compile_relationships(
            draft, self.compiler.definitions, diagnostics
        )
        background = _compile_background(
            draft, base.build, self.compiler.definitions, self.context, diagnostics
        )
        wealth = _compile_wealth(draft, background, self.context, diagnostics)
        _validate_identities(draft, diagnostics)
        spent, remaining = _validate_balance(
            base.spent,
            relationship_points,
            self.compiler.policy.point_budget,
            self.context.require_exact_point_budget,
            diagnostics,
        )

        character = None
        if not diagnostics and not dry_run:
            assert base.build is not None and background is not None and wealth is not None
            payload = {
                "build_revision": base.build.revision,
                "construction": draft.model_dump(mode="json"),
                "context": self.context.model_dump(mode="json"),
                "relationship_points": relationship_points,
            }
            revision = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            character = ConstructedCharacter(
                revision,
                base.build,
                spent,
                background,
                draft.physical,
                relationships,
                draft.identities,
                wealth,
            )
        return ConstructionCompilation(tuple(diagnostics), spent, remaining, character)
