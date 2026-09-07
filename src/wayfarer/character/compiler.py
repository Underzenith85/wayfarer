"""Server-owned compilation of untrusted drafts against exact campaign pins."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as SchemaError

from wayfarer.character import statistics
from wayfarer.character.skills import SkillCompiler, SkillError
from wayfarer.character.statistics import (
    ATTRIBUTE_IDS,
    SECONDARY_IDS,
    Attribute,
    CharacterStatistics,
    PrimaryAttributes,
    Secondary,
    SecondaryLevels,
    StatisticsError,
)
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
from wayfarer.rules.traits import TraitOptions
from wayfarer.rules.traits import cost as trait_cost


class Purchase(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    definition_id: str = Field(min_length=1, max_length=200)
    amount: int = Field(default=1, ge=1, le=10000)
    trait: TraitOptions | None = Field(default=None, exclude_if=lambda value: value is None)


class CharacterDraft(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
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
    statistics: CharacterStatistics | None = None
    trait_purchases: tuple[Purchase, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Mutable resources are separate from the immutable purchased revision."""

    build_revision: str
    hp: int
    fp: int


_PROTOTYPE_POOLS: Final = MappingProxyType({"hp": "attribute:st", "fp": "attribute:ht"})
_PROFILE_POOLS: Final = MappingProxyType({"hp": "secondary:hp", "fp": "secondary:fp"})


def pool_limits(build: ValidatedBuild) -> dict[str, int]:
    """Runtime pool maxima a build implies; the only place that mapping is decided.

    Prototype builds keep the original ST/HT initialization. Profile builds use
    the purchased HP and FP from the statistics projection, after effects.
    """

    values = {v.target: v.value for v in build.sheet.values}
    targets = _PROTOTYPE_POOLS if build.statistics is None else _PROFILE_POOLS
    if not set(targets.values()) <= values.keys():
        raise ValidationError("No implemented runtime resource initialization")
    limits: dict[str, int] = {}
    for kind, target in targets.items():
        value = values[target]
        if not value.is_finite() or value != value.to_integral_value() or value < 0:
            raise ValidationError(f"Runtime pool limit is not a whole number: {kind}")
        limits[kind] = int(value)
    return limits


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
        statistics_profile: str | None = None,
        trait_runtime_hooks: frozenset[str] = frozenset(),
    ) -> None:
        self.rules, self.policy = rules, policy
        self.trait_runtime_hooks = trait_runtime_hooks
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
        self.definition_packages = {
            d.id: (p.id, p.version) for p in packages for d in p.definitions
        }
        if len(self.definitions) != len(definitions):
            raise ValidationError("Ambiguous definition IDs")
        # Exact profile selection: statistics never activate from a package name,
        # and a package cannot smuggle secondary characteristics without a profile.
        self.statistics_profile = statistics_profile
        self.statistics = (
            None if statistics_profile is None else statistics.rules(statistics_profile)
        )
        if self.statistics is None:
            if any(d.kind is DefinitionKind.SECONDARY for d in definitions):
                raise ValidationError("Secondary characteristics require a selected rules profile")
        else:
            for expected in statistics.definitions(self.statistics.profile_id):
                actual = self.definitions.get(expected.id)
                if actual is None or (actual.kind, actual.point_cost, actual.status) != (
                    expected.kind,
                    expected.point_cost,
                    expected.status,
                ):
                    raise ValidationError(
                        f"Pinned packages do not carry profile statistics: {expected.id}"
                    )
        self.skills = (
            None
            if statistics_profile is None
            else SkillCompiler(
                statistics_profile,
                self.definitions,
                frozenset(
                    d.id
                    for d in definitions
                    if d.source_id in policy.permitted_sources
                    and (policy.allow_supernatural or "supernatural" not in d.hooks)
                ),
            )
        )
        if self.skills is None and any(d.skill is not None for d in definitions):
            raise ValidationError("GURPS skills require an exact selected rules profile")
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
        secondary_levels: dict[Secondary, int] = {}
        deferred: list[tuple[str, int]] = []
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
            if purchase.trait is not None and definition.trait_rules is None:
                error("trait.unsupported", i, "Trait options require catalog metadata")
            if definition.parameters and definition.trait_rules is None:
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
                minimum = 8 if self.statistics is None else self.statistics.attribute_minimum
                if not minimum <= amount <= self.policy.attribute_ceiling:
                    error("attribute.range", i, "Attribute is outside campaign bounds")
                if self.statistics is not None and key in ATTRIBUTE_IDS:
                    cost = statistics.attribute_cost(
                        self.statistics.profile_id, ATTRIBUTE_IDS[key], max(amount, minimum)
                    )
                elif cost is not None:
                    cost *= amount - 10
                bases[key] = Decimal(amount)
            elif definition.kind is DefinitionKind.SECONDARY:
                if self.statistics is None or key not in SECONDARY_IDS:
                    error("secondary.unsupported", i, "No selected profile compiles this value")
                    continue
                # Costs depend on the effective attributes, so they resolve below.
                secondary_levels[SECONDARY_IDS[key]] = amount
                deferred.append((key, amount))
                continue
            elif definition.kind is DefinitionKind.SKILL:
                if self.skills is not None:
                    if key not in self.skills.specs:
                        error("skill.unsupported", i, "No pinned GURPS skill specification")
                else:
                    # Preserve the original prototype cost curve and dispatch.
                    if definition.name not in SKILLS or "character.skill" not in definition.hooks:
                        error("skill.unsupported", i, "No implemented skill cost curve")
                    if amount not in (1, 2, 4, 8, 12, 16):
                        error("skill.points", i, "Invalid skill point allocation")
                cost = amount
            elif definition.kind is DefinitionKind.TRAIT:
                metadata = definition.trait_rules
                if metadata is None:
                    if amount != 1:
                        error("trait.amount", i, "This trait is not leveled")
                elif metadata.profile_id != self.statistics_profile:
                    error("trait.profile", i, "Trait requires its exact selected profile")
                elif cost is not None:
                    options = purchase.trait or TraitOptions()
                    try:
                        cost = trait_cost(cost, amount, options, metadata)
                        if any(hook.startswith("ability:") for hook in metadata.runtime_hooks):
                            from wayfarer.rules.abilities import validate_purchase

                            validate_purchase(definition, amount, options)
                    except ValidationError as exc:
                        error("trait.invalid", i, str(exc))
                    required_hooks = set(metadata.runtime_hooks)
                    required_hooks.update(
                        m.runtime_hook
                        for m in metadata.modifiers
                        if m.id in options.modifiers and m.runtime_hook is not None
                    )
                    if metadata.self_control:
                        required_hooks.add("trait.self_control")
                    if not required_hooks <= self.trait_runtime_hooks:
                        error("trait.runtime_unavailable", i, "Trait runtime hook is unavailable")
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
        projection = None
        if self.statistics is not None:
            projection = self._statistics(effective_attributes, secondary_levels, diagnostics)
            if projection is not None:
                for key, amount in deferred:
                    cost = projection.costs.secondaries[SECONDARY_IDS[key]]
                    spent += cost
                    disadvantages += max(0, -cost)
                    entries.append(PurchasedEntry(key, amount, cost))
                bases.update(projection.target_values())
        for purchase in draft.purchases:
            definition = self.definitions.get(purchase.definition_id)
            if (
                self.skills is None
                and definition
                and definition.kind is DefinitionKind.SKILL
                and definition.name in SKILLS
            ):
                attribute, offset = SKILLS[definition.name]
                base = effective_attributes.get(f"attribute:{attribute.lower()}")
                if base is not None:
                    points = purchase.amount
                    bases[definition.id] = (
                        base + offset + {1: 0, 2: 1}.get(points, 2 + (points - 4) // 4)
                    )
        if self.skills is not None and projection is not None and not diagnostics:
            # Skills use effective primary and purchased secondary attributes.
            secondary_evaluator = EffectEvaluator(tuple(MechanicalTarget(k) for k in bases))
            skill_attributes = {
                **effective_attributes,
                **{
                    key: secondary_evaluator.evaluate(key, base, effects, context={}, at=0).value
                    for key, base in bases.items()
                    if key.startswith("secondary:")
                },
            }
            skill_evaluator = EffectEvaluator(tuple(MechanicalTarget(k) for k in self.skills.specs))

            def adjust_skill(key: str, base: int) -> int:
                adjusted = skill_evaluator.evaluate(
                    key, Decimal(base), effects, context={}, at=0
                ).value
                if not adjusted.is_finite() or adjusted != adjusted.to_integral_value():
                    raise SkillError(
                        "skill.level", "Skill effects must produce whole-number levels"
                    )
                return int(adjusted)

            try:
                compiled_skills = self.skills.compile(
                    {
                        p.definition_id: p.amount
                        for p in draft.purchases
                        if p.definition_id in self.skills.specs
                    },
                    skill_attributes,
                    adjust_skill,
                )
                bases.update(
                    {
                        s.target: Decimal(s.unmodified if s.unmodified is not None else s.level)
                        for s in compiled_skills
                    }
                )
            except SkillError as exc:
                diagnostics.append(Diagnostic(exc.code, ("purchases",), str(exc)))
        target_ids = set(bases) | {e.target for e in effects}
        if self.skills is not None:
            # An effect cannot manufacture access to a skill with no legal default.
            target_ids = {key for key in target_ids if key in bases or key not in self.skills.specs}
        targets = tuple(sorted(target_ids))
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
                if self.skills is None or value_.target in selected:
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
            if projection is not None:
                payload["statistics"] = asdict(projection)
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
                projection,
                tuple(p for p in draft.purchases if self.definitions[p.definition_id].trait_rules),
            )
        return Compilation(tuple(diagnostics), spent, self.policy.point_budget - spent, build)

    def _statistics(
        self,
        effective: dict[str, Decimal],
        levels: dict[Secondary, int],
        diagnostics: list[Diagnostic],
    ) -> CharacterStatistics | None:
        assert self.statistics is not None
        attributes: dict[Attribute, int] = {}
        for key, attribute in ATTRIBUTE_IDS.items():
            value = effective.get(key)
            if value is None:
                return None  # attribute.required already reported
            if not value.is_finite() or value != value.to_integral_value():
                diagnostics.append(
                    Diagnostic("derived.range", ("sheet", key), "Attribute must be a whole number")
                )
                return None
            attributes[attribute] = int(value)
        try:
            return statistics.compile_statistics(
                self.statistics.profile_id,
                PrimaryAttributes(
                    attributes[Attribute.ST],
                    attributes[Attribute.DX],
                    attributes[Attribute.IQ],
                    attributes[Attribute.HT],
                ),
                SecondaryLevels(
                    hp=levels.get(Secondary.HP),
                    will=levels.get(Secondary.WILL),
                    per=levels.get(Secondary.PER),
                    fp=levels.get(Secondary.FP),
                    basic_speed=levels.get(Secondary.BASIC_SPEED),
                    basic_move=levels.get(Secondary.BASIC_MOVE),
                ),
            )
        except StatisticsError as exc:
            diagnostics.append(Diagnostic(exc.code, ("sheet",), str(exc)))
            return None

    def activate(
        self, value: object, *, authorize: Callable[[ValidatedBuild], None]
    ) -> tuple[ValidatedBuild, RuntimeState]:
        """Recompile drafts at activation; never accept a client-provided build."""
        result = self.compile(value)
        if result.build is None:
            raise ValidationError("; ".join(d.code for d in result.diagnostics))
        authorize(result.build)
        limits = pool_limits(result.build)
        return result.build, RuntimeState(result.build.revision, limits["hp"], limits["fp"])

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
