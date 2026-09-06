"""Server-owned compilation of untrusted drafts against exact campaign pins."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    SKILLS,
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    RulesCatalog,
)
from wayfarer.rules.effects import DerivedValue, Effect, EffectEvaluator, MechanicalTarget


class Purchase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    definition_id: str = Field(min_length=1, max_length=200)
    amount: int = Field(default=1, ge=1, le=10000)


class CharacterDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str = Field(min_length=1, max_length=200)
    backstory: str = Field(default="", max_length=10000)
    purchases: tuple[Purchase, ...] = Field(default=(), strict=False, max_length=1000)


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    path: tuple[str | int, ...]
    message: str


@dataclass(frozen=True, slots=True)
class PurchasedEntry:
    definition_id: str
    amount: int
    cost: int


@dataclass(frozen=True, slots=True)
class DerivedSheet:
    values: tuple[DerivedValue, ...]


@dataclass(frozen=True, slots=True)
class ValidatedBuild:
    revision: str
    rules: CampaignRules
    purchases: tuple[PurchasedEntry, ...]
    spent: int
    disadvantages: int
    sheet: DerivedSheet
    name: str
    backstory: str


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Mutable resources are separate from the immutable purchased revision."""

    build_revision: str
    hp: int
    fp: int


@dataclass(frozen=True, slots=True)
class Compilation:
    diagnostics: tuple[Diagnostic, ...]
    spent: int
    remaining: int
    build: ValidatedBuild | None

    @property
    def legal(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True, slots=True)
class RepairProposal:
    draft: CharacterDraft
    compilation: Compilation


class CharacterCompiler:
    """A trusted server creates this with catalog-backed effect bindings.

    Public requests supply only drafts; costs, effects, rules and policy come
    from this configured compiler. Dry runs use the exact activation checks.
    """

    def __init__(
        self,
        catalog: RulesCatalog,
        rules: CampaignRules,
        policy: CampaignPolicy,
        effects: tuple[tuple[str, Effect], ...] = (),
    ) -> None:
        self.rules, self.policy = rules, policy
        if (rules.policy_id, rules.policy_version) != (policy.id, policy.version):
            raise ValidationError("Campaign policy pin does not resolve")
        if not rules.packages or len(set(rules.packages)) != len(rules.packages):
            raise ValidationError("Empty or duplicate package pins")
        packages = tuple(catalog.package(pin) for pin in rules.packages)
        if any(p.edition != rules.edition for p in packages):
            raise ValidationError("Rules edition mismatch")
        if any(dep not in {p.id for p in packages} for p in packages for dep in p.dependencies):
            raise ValidationError("Missing pinned package dependency")
        definitions = tuple(d for p in packages for d in p.definitions)
        self.definitions = {d.id: d for d in definitions}
        if len(self.definitions) != len(definitions):
            raise ValidationError("Ambiguous definition IDs")
        if any(
            type(v) is not int or v < 0
            for v in (
                policy.point_budget,
                policy.disadvantage_limit,
                policy.attribute_ceiling,
                policy.skill_ceiling,
            )
        ):
            raise ValidationError("Invalid campaign limits")
        self.effects = effects
        if len({e.id for _, e in effects}) != len(effects):
            raise ValidationError("Duplicate effect IDs")
        for definition_id, effect in effects:
            if not effect.value.is_finite():
                raise ValidationError("Non-finite mechanical effect")
            definition = self.definitions.get(definition_id)
            if definition is None or definition.status is not ImplementationStatus.IMPLEMENTED:
                raise ValidationError("Effect requires an implemented catalog definition")
            if not any(
                definition in p.definitions
                and effect.source_id == definition_id
                and effect.source_version == p.version
                for p in packages
            ):
                raise ValidationError("Effect provenance does not match its pinned definition")

    def compile(self, value: object, *, dry_run: bool = False) -> Compilation:
        diagnostics: list[Diagnostic] = []
        try:
            draft = CharacterDraft.model_validate(value)
        except SchemaError as exc:
            return Compilation(
                tuple(
                    Diagnostic("draft.schema", e["loc"], "Invalid or unexpected draft field")
                    for e in exc.errors()
                ),
                0,
                self.policy.point_budget,
                None,
            )
        selected = {p.definition_id for p in draft.purchases}
        seen: set[str] = set()
        entries: list[PurchasedEntry] = []
        bases: dict[str, Decimal] = {}
        spent = disadvantages = 0

        def error(code: str, index: int, message: str) -> None:
            diagnostics.append(Diagnostic(code, ("purchases", index), message))

        for i, purchase in enumerate(draft.purchases):
            key, amount = purchase.definition_id, purchase.amount
            if key in seen:
                error("purchase.duplicate", i, "A definition can be purchased only once")
                continue
            seen.add(key)
            definition = self.definitions.get(key)
            if definition is None:
                error("definition.unknown", i, "Unknown definition")
                continue
            if definition.status is not ImplementationStatus.IMPLEMENTED:
                error("definition.not_implemented", i, "Automatic activation is unavailable")
            if definition.source_id not in self.policy.permitted_sources:
                error("source.forbidden", i, "Source is not permitted")
            if definition.parameters:
                error(
                    "definition.parameters_unsupported",
                    i,
                    "Parameterized purchases require a compiler",
                )
            if "supernatural" in definition.hooks and not self.policy.allow_supernatural:
                error("policy.supernatural", i, "Supernatural abilities are not permitted")
            if any(p not in selected for p in definition.prerequisites):
                error("purchase.prerequisite", i, "Missing prerequisite")
            if any(p in selected for p in definition.exclusions):
                error("purchase.exclusion", i, "Mutually exclusive purchases")
            cost = definition.point_cost
            if definition.kind is DefinitionKind.ATTRIBUTE:
                if not 8 <= amount <= self.policy.attribute_ceiling:
                    error("attribute.range", i, "Attribute is outside campaign bounds")
                if cost is not None:
                    cost *= amount - 10
                bases[key] = Decimal(amount)
            elif definition.kind is DefinitionKind.SKILL:
                # This curve is explicitly the original prototype package's mechanic.
                if definition.name not in SKILLS or "character.skill" not in definition.hooks:
                    error("skill.unsupported", i, "No implemented skill cost curve")
                if amount not in (1, 2, 4, 8, 12, 16):
                    error("skill.points", i, "Invalid skill point allocation")
                cost = amount
            elif definition.kind is DefinitionKind.TRAIT:
                if amount != 1:
                    error("trait.amount", i, "This trait is not leveled")
            else:
                error("purchase.kind", i, "Equipment is acquired through inventory commands")
            if cost is None or type(cost) is not int:
                error("cost.unsupported", i, "No implemented point cost")
                continue
            spent += cost
            disadvantages += max(0, -cost)
            entries.append(PurchasedEntry(key, amount, cost))
        for definition in self.definitions.values():
            if definition.kind is DefinitionKind.ATTRIBUTE and definition.id not in selected:
                diagnostics.append(Diagnostic("attribute.required", ("purchases",), definition.id))
        effects = tuple(e for key, e in self.effects if key in selected)
        attribute_evaluator = EffectEvaluator(tuple(MechanicalTarget(key) for key in bases))
        effective_attributes = {
            key: attribute_evaluator.evaluate(key, base, effects, context={}, at=0).value
            for key, base in bases.items()
        }
        for purchase in draft.purchases:
            definition = self.definitions.get(purchase.definition_id)
            if definition and definition.kind is DefinitionKind.SKILL and definition.name in SKILLS:
                attribute, offset = SKILLS[definition.name]
                base = effective_attributes.get(f"attribute:{attribute.lower()}")
                if base is not None:
                    points = purchase.amount
                    bases[definition.id] = (
                        base + offset + {1: 0, 2: 1}.get(points, 2 + (points - 4) // 4)
                    )
        targets = tuple(sorted(set(bases) | {e.target for e in effects}))
        evaluator = EffectEvaluator(tuple(MechanicalTarget(key) for key in targets))
        sheet = DerivedSheet(
            tuple(
                evaluator.evaluate(key, bases.get(key, Decimal(0)), effects, context={}, at=0)
                for key in targets
            )
        )
        for value_ in sheet.values:
            definition = self.definitions.get(value_.target)
            ceiling = None
            if definition and definition.kind is DefinitionKind.ATTRIBUTE:
                ceiling = self.policy.attribute_ceiling
            elif definition and definition.kind is DefinitionKind.SKILL:
                ceiling = self.policy.skill_ceiling
            if ceiling is not None and (
                value_.value < 1 or value_.value != value_.value.to_integral_value()
            ):
                diagnostics.append(
                    Diagnostic(
                        "derived.range",
                        ("sheet", value_.target),
                        "Derived value must be a positive integer",
                    )
                )
            if ceiling is not None and value_.value > ceiling:
                diagnostics.append(
                    Diagnostic(
                        "derived.ceiling",
                        ("sheet", value_.target),
                        "Derived value exceeds campaign ceiling",
                    )
                )
        if disadvantages > self.policy.disadvantage_limit:
            diagnostics.append(
                Diagnostic("budget.disadvantages", ("purchases",), "Disadvantage limit exceeded")
            )
        if spent > self.policy.point_budget:
            diagnostics.append(
                Diagnostic("budget.overspent", ("purchases",), "Point budget exceeded")
            )
        build = None
        if not diagnostics and not dry_run:
            payload = {
                "draft": draft.model_dump(mode="json"),
                "rules": asdict(self.rules),
                "policy": {
                    **asdict(self.policy),
                    "permitted_sources": sorted(self.policy.permitted_sources),
                    "allowed_equipment": sorted(self.policy.allowed_equipment),
                },
                "entries": [asdict(e) for e in entries],
                "sheet": asdict(sheet),
            }
            revision = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
            ).hexdigest()
            build = ValidatedBuild(
                revision,
                self.rules,
                tuple(entries),
                spent,
                disadvantages,
                sheet,
                draft.name,
                draft.backstory,
            )
        return Compilation(tuple(diagnostics), spent, self.policy.point_budget - spent, build)

    def activate(self, value: object) -> tuple[ValidatedBuild, RuntimeState]:
        """Recompile drafts at activation; never accept a client-provided build."""
        result = self.compile(value)
        if result.build is None:
            raise ValidationError("; ".join(d.code for d in result.diagnostics))
        values = {v.target: v.value for v in result.build.sheet.values}
        if not {"attribute:st", "attribute:ht"} <= values.keys():
            raise ValidationError("No implemented runtime resource initialization")
        return result.build, RuntimeState(
            result.build.revision, int(values["attribute:st"]), int(values["attribute:ht"])
        )

    def repair(self, value: object) -> RepairProposal | None:
        """Offer a duplicate-removal candidate only when the entire result revalidates."""
        try:
            draft = CharacterDraft.model_validate(value)
        except SchemaError:
            return None
        unique = {p.definition_id: p for p in reversed(draft.purchases)}
        candidate = CharacterDraft(
            name=draft.name,
            backstory=draft.backstory,
            purchases=tuple(reversed(tuple(unique.values()))),
        )
        verdict = self.compile(candidate, dry_run=True)
        return RepairProposal(candidate, verdict) if verdict.legal else None
