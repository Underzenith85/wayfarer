"""Authoritative character, racial, sub-race and meta-trait composition.

Templates select owned compiler inputs. They never supply a caller-controlled
price or make an unavailable definition executable (Characters B258-263;
Campaigns B445-454).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Compilation,
    Diagnostic,
    Purchase,
    ValidatedBuild,
)
from wayfarer.engine.rules.catalog import DefinitionKind
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import PROFILE
from wayfarer.errors import ValidationError
from wayfarer.models import Record

OriginKind = Literal["personal", "racial", "sub-race", "occupational", "meta-trait"]


class TemplateComponent(Record):
    """One owned compiler input; additive amounts model racial attribute modifiers."""

    purchase: Purchase
    mode: Literal["highest", "additive"] = "highest"


class TemplateOption(Record):
    id: str = Field(min_length=1, max_length=100)
    purchases: tuple[Purchase, ...] = Field(strict=False, min_length=1, max_length=100)
    excludes: tuple[str, ...] = Field(default=(), strict=False, max_length=100)


class TemplateChoice(Record):
    id: str = Field(min_length=1, max_length=100)
    count: int | None = Field(default=None, ge=0, le=100)
    minimum_count: int = Field(default=1, ge=0, le=100)
    maximum_count: int = Field(default=1, ge=0, le=100)
    point_budget: int | None = Field(default=None, ge=0, le=10000)
    options: tuple[TemplateOption, ...] = Field(default=(), strict=False, max_length=100)
    allowed_definition_ids: tuple[str, ...] = Field(default=(), strict=False, max_length=100)

    @property
    def limits(self) -> tuple[int, int]:
        return (
            (self.count, self.count)
            if self.count is not None
            else (self.minimum_count, self.maximum_count)
        )

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("Duplicate template option")
        option_ids = {option.id for option in self.options}
        if any(not set(option.excludes) <= option_ids for option in self.options):
            raise ValueError("Template option excludes an unknown option")
        if len(set(self.allowed_definition_ids)) != len(self.allowed_definition_ids):
            raise ValueError("Duplicate allocated definition")
        minimum, maximum = self.limits
        capacity = len(self.options) + len(self.allowed_definition_ids)
        if capacity == 0 or minimum > maximum or maximum > capacity:
            raise ValueError("Template choice exceeds its available options")
        return self


class TemplateAdjustment(Record):
    """A trusted, source-defined adjustment; never accepted in a selection."""

    id: str = Field(min_length=1, max_length=100)
    points: int = Field(ge=-10000, le=10000)
    reason: str = Field(min_length=1, max_length=500)
    reference: str = Field(min_length=1, max_length=100)
    counts_as_disadvantage: bool = False


class Template(Record):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["racial", "occupational", "meta-trait"]
    origin: Literal["publisher", "campaign", "player"] = "publisher"
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    purchases: tuple[Purchase, ...] = Field(default=(), strict=False, max_length=100)
    components: tuple[TemplateComponent, ...] = Field(default=(), strict=False, max_length=100)
    includes: tuple[str, ...] = Field(default=(), strict=False, max_length=20)
    choices: tuple[TemplateChoice, ...] = Field(default=(), strict=False, max_length=20)
    taboo_traits: tuple[str, ...] = Field(default=(), strict=False, max_length=100)
    omittable_traits: tuple[str, ...] = Field(default=(), strict=False, max_length=100)
    adjustments: tuple[TemplateAdjustment, ...] = Field(default=(), strict=False, max_length=20)
    reference: str = "B258-263; B445-454; original representative construction"

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.includes)) != len(self.includes):
            raise ValueError("Duplicate included template")
        if len({choice.id for choice in self.choices}) != len(self.choices):
            raise ValueError("Duplicate template choice")
        if self.kind in {"racial", "meta-trait"} and self.choices:
            raise ValueError("Racial and meta-trait components cannot be optional")
        if self.kind != "racial" and self.omittable_traits:
            raise ValueError("Only racial traits can be omittable")
        if len(set(self.omittable_traits)) != len(self.omittable_traits):
            raise ValueError("Duplicate omittable racial trait")
        if len({entry.id for entry in self.adjustments}) != len(self.adjustments):
            raise ValueError("Duplicate template adjustment")
        return self


class Selection(Record):
    template_id: str
    choice_id: str
    option_ids: tuple[str, ...] = Field(default=(), strict=False, max_length=100)
    allocations: tuple[Purchase, ...] = Field(default=(), strict=False, max_length=100)


class RacialOmission(Record):
    template_id: str
    definition_ids: tuple[str, ...] = Field(strict=False, min_length=1, max_length=100)


class PurchaseOrigin(Record):
    definition_id: str
    origin: OriginKind
    amount: int
    template_id: str | None = None


class AppliedAdjustment(Record):
    id: str
    template_id: str
    points: int
    reason: str
    counts_as_disadvantage: bool = False


@dataclass(frozen=True, slots=True)
class TemplateCompilation:
    diagnostics: tuple[Diagnostic, ...]
    spent: int
    remaining: int
    build: ValidatedBuild | None

    @property
    def legal(self) -> bool:
        return not self.diagnostics


@dataclass(frozen=True, slots=True)
class TemplatePreview:
    draft: CharacterDraft
    template_ids: tuple[str, ...]
    template_digest: str
    compilation: TemplateCompilation
    provenance: tuple[PurchaseOrigin, ...] = ()
    adjustments: tuple[AppliedAdjustment, ...] = ()
    selections: tuple[Selection, ...] = ()
    omissions: tuple[RacialOmission, ...] = ()
    root_template_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Contribution:
    purchase: Purchase
    origin: PurchaseOrigin
    mode: Literal["highest", "additive"] = "highest"
    choice_key: tuple[str, str] | None = None


def _origin(template: Template, templates: dict[str, Template]) -> OriginKind:
    if template.kind == "meta-trait":
        return "meta-trait"
    if template.kind == "occupational":
        return "occupational"
    if any(templates[parent].kind == "racial" for parent in template.includes):
        return "sub-race"
    return "racial"


def _compatible(left: Purchase, right: Purchase) -> bool:
    return left.trait == right.trait and left.technology_level == right.technology_level


def _merge(contributions: list[_Contribution], compiler: CharacterCompiler) -> CharacterDraft:
    grouped: dict[str, list[_Contribution]] = defaultdict(list)
    for entry in contributions:
        grouped[entry.purchase.definition_id].append(entry)
    purchases: list[Purchase] = []
    for identifier, entries in grouped.items():
        first = entries[0].purchase
        if any(not _compatible(first, entry.purchase) for entry in entries[1:]):
            raise ValidationError("Conflicting template purchases require explicit reconciliation")
        additive = [entry.purchase.amount for entry in entries if entry.mode == "additive"]
        highest = [entry.purchase.amount for entry in entries if entry.mode == "highest"]
        definition = compiler.definitions.get(identifier)
        if additive and (
            definition is None
            or definition.kind not in {DefinitionKind.ATTRIBUTE, DefinitionKind.SECONDARY}
        ):
            raise ValidationError("Additive template components require an attribute")
        amount = (max(highest) if highest else 0) + sum(additive)
        if amount < 1:
            raise ValidationError("Template composition produced an invalid purchase amount")
        purchases.append(first.model_copy(update={"amount": amount}))
    return CharacterDraft(name="template", purchases=tuple(purchases))


class TemplateCatalog:
    """Trusted template definitions bound to one compiler and campaign permission."""

    def __init__(
        self,
        templates: tuple[Template, ...],
        compiler: CharacterCompiler,
        *,
        allow_player_created_races: bool = False,
    ) -> None:
        if compiler.statistics_profile != PROFILE:
            raise ValidationError("Templates require the exact Basic Set profile")
        checked = tuple(Template.model_validate(template) for template in templates)
        self._templates = {template.id: template for template in checked}
        self._compiler = compiler
        self._allow_player_created_races = allow_player_created_races
        if len(self._templates) != len(checked):
            raise ValidationError("Duplicate template identifier")
        self._validate_catalog(checked)
        for identifier in self._templates:
            self._expand(identifier, (), set())
        self.digest = hashlib.sha256(
            json.dumps(
                {
                    "templates": [
                        self._templates[key].model_dump(mode="json")
                        for key in sorted(self._templates)
                    ],
                    "rules": asdict(compiler.rules),
                    "player_races": allow_player_created_races,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def _validate_catalog(self, templates: tuple[Template, ...]) -> None:
        for template in templates:
            if not set(template.includes) <= self._templates.keys():
                raise ValidationError("Unresolved included template")
            purchases = (
                *template.purchases,
                *(component.purchase for component in template.components),
                *(
                    purchase
                    for choice in template.choices
                    for option in choice.options
                    for purchase in option.purchases
                ),
            )
            references = {purchase.definition_id for purchase in purchases}
            references |= {
                identifier
                for choice in template.choices
                for identifier in choice.allowed_definition_ids
            }
            references |= set(template.taboo_traits) | set(template.omittable_traits)
            if not references <= self._compiler.definitions.keys():
                raise ValidationError("Unresolved template purchase or taboo trait")
            own = {purchase.definition_id for purchase in template.purchases}
            own |= {component.purchase.definition_id for component in template.components}
            if not set(template.omittable_traits) <= own:
                raise ValidationError("Omittable racial trait must be owned by that template")

    def _expand(self, identifier: str, trail: tuple[str, ...], seen: set[str]) -> list[Template]:
        if identifier in trail:
            raise ValidationError("Template inclusion cycle")
        if identifier not in self._templates:
            raise ValidationError("Unknown template")
        if identifier in seen:
            raise ValidationError("Template included more than once")
        seen.add(identifier)
        template = self._templates[identifier]
        result = []
        for parent in template.includes:
            result.extend(self._expand(parent, (*trail, identifier), seen))
        result.append(template)
        return result

    def _selected_templates(self, identifiers: tuple[str, ...]) -> list[Template]:
        if not identifiers:
            raise ValidationError("Select at least one template")
        result: list[Template] = []
        seen: set[str] = set()
        for identifier in identifiers:
            result.extend(self._expand(identifier, (), seen))
        player_race = any(
            template.kind == "racial" and template.origin == "player" for template in result
        )
        if player_race and not self._allow_player_created_races:
            raise ValidationError("Player-created races require campaign permission")
        return result

    @staticmethod
    def _selection_map(
        templates: list[Template], selections: tuple[Selection, ...]
    ) -> dict[tuple[str, str], Selection]:
        supplied: dict[tuple[str, str], Selection] = {}
        for raw in selections:
            selection = Selection.model_validate(raw)
            key = (selection.template_id, selection.choice_id)
            if key in supplied:
                raise ValidationError("Duplicate template selection")
            supplied[key] = selection
        choices = {
            (template.id, choice.id): choice
            for template in templates
            for choice in template.choices
        }
        required = {key for key, choice in choices.items() if choice.limits[0] > 0}
        if not required <= supplied.keys() or not supplied.keys() <= choices.keys():
            raise ValidationError("Missing or unexpected template selections")
        for key, selection in supplied.items():
            choice = choices[key]
            minimum, maximum = choice.limits
            options = {option.id: option for option in choice.options}
            chosen = selection.option_ids
            allocated = [entry.definition_id for entry in selection.allocations]
            count = len(chosen) + len(allocated)
            if not minimum <= count <= maximum or len(set(chosen)) != len(chosen):
                raise ValidationError("Invalid template choice")
            if not set(chosen) <= options.keys():
                raise ValidationError("Invalid template choice")
            if len(set(allocated)) != len(allocated):
                raise ValidationError("Invalid template choice")
            if not set(allocated) <= set(choice.allowed_definition_ids):
                raise ValidationError("Invalid template choice")
            if any(set(options[option_id].excludes).intersection(chosen) for option_id in chosen):
                raise ValidationError("Invalid template choice")
        return supplied

    @staticmethod
    def _omission_map(
        templates: list[Template], omissions: tuple[RacialOmission, ...]
    ) -> set[tuple[str, str]]:
        by_id = {template.id: template for template in templates}
        omitted: set[tuple[str, str]] = set()
        for raw in omissions:
            omission = RacialOmission.model_validate(raw)
            template = by_id.get(omission.template_id)
            if template is None or template.kind != "racial":
                raise ValidationError("Racial omission references an unselected racial template")
            if len(set(omission.definition_ids)) != len(omission.definition_ids):
                raise ValidationError("Duplicate omitted racial trait")
            if not set(omission.definition_ids) <= set(template.omittable_traits):
                raise ValidationError("Racial trait is not omittable")
            for identifier in omission.definition_ids:
                key = (template.id, identifier)
                if key in omitted:
                    raise ValidationError("Duplicate omitted racial trait")
                omitted.add(key)
        return omitted

    def _contributions(
        self,
        draft: CharacterDraft,
        templates: list[Template],
        supplied: dict[tuple[str, str], Selection],
        omitted: set[tuple[str, str]],
        *,
        include_omitted: bool,
    ) -> list[_Contribution]:
        result = [
            _Contribution(
                purchase,
                PurchaseOrigin(
                    definition_id=purchase.definition_id,
                    origin="personal",
                    amount=purchase.amount,
                ),
            )
            for purchase in draft.purchases
        ]
        for template in templates:
            origin = _origin(template, self._templates)
            components = (
                *(TemplateComponent(purchase=purchase) for purchase in template.purchases),
                *template.components,
            )
            for component in components:
                purchase = component.purchase
                if (template.id, purchase.definition_id) in omitted and not include_omitted:
                    continue
                result.append(
                    _Contribution(
                        purchase,
                        PurchaseOrigin(
                            definition_id=purchase.definition_id,
                            origin=origin,
                            amount=purchase.amount,
                            template_id=template.id,
                        ),
                        component.mode,
                    )
                )
            for choice in template.choices:
                options = {option.id: option for option in choice.options}
                selection = supplied.get(
                    (template.id, choice.id),
                    Selection(template_id=template.id, choice_id=choice.id),
                )
                for option_id in sorted(selection.option_ids):
                    for purchase in options[option_id].purchases:
                        result.append(
                            _Contribution(
                                purchase,
                                PurchaseOrigin(
                                    definition_id=purchase.definition_id,
                                    origin=origin,
                                    amount=purchase.amount,
                                    template_id=template.id,
                                ),
                                choice_key=(template.id, choice.id),
                            )
                        )
                for purchase in selection.allocations:
                    result.append(
                        _Contribution(
                            purchase,
                            PurchaseOrigin(
                                definition_id=purchase.definition_id,
                                origin=origin,
                                amount=purchase.amount,
                                template_id=template.id,
                            ),
                            choice_key=(template.id, choice.id),
                        )
                    )
        return result

    def _validate_choice_budgets(
        self,
        templates: list[Template],
        contributions: list[_Contribution],
        full: Compilation,
    ) -> None:
        for template in templates:
            for choice in template.choices:
                if choice.point_budget is None:
                    continue
                key = (template.id, choice.id)
                if not any(entry.choice_key == key for entry in contributions):
                    continue
                without = [entry for entry in contributions if entry.choice_key != key]
                draft = _merge(without, self._compiler)
                cost = full.spent - self._compiler.compile(draft, dry_run=True).spent
                if cost != choice.point_budget:
                    raise ValidationError("Template point allocation does not match compiler cost")

    def _apply_adjustments(
        self,
        base: Compilation,
        adjustments: tuple[AppliedAdjustment, ...],
    ) -> TemplateCompilation:
        adjustment = sum(entry.points for entry in adjustments)
        spent = base.spent + adjustment
        remaining = self._compiler.policy.point_budget - spent
        diagnostics = list(base.diagnostics)
        overspent = spent > self._compiler.policy.point_budget
        if overspent and not any(entry.code == "budget.overspent" for entry in diagnostics):
            diagnostics.append(
                Diagnostic("budget.overspent", ("templates",), "Point budget exceeded")
            )
        build = base.build
        if build is not None and not diagnostics:
            disadvantages = build.disadvantages + sum(
                max(0, -entry.points) for entry in adjustments if entry.counts_as_disadvantage
            )
            if disadvantages > self._compiler.policy.disadvantage_limit:
                diagnostics.append(
                    Diagnostic(
                        "budget.disadvantages",
                        ("templates",),
                        "Disadvantage limit exceeded",
                    )
                )
                build = None
            else:
                payload = {
                    "build": build.revision,
                    "adjustments": [entry.model_dump(mode="json") for entry in adjustments],
                }
                revision = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                build = replace(
                    build,
                    revision=revision,
                    spent=spent,
                    disadvantages=disadvantages,
                )
        return TemplateCompilation(tuple(diagnostics), spent, remaining, build)

    def preview(
        self,
        draft: CharacterDraft,
        template_ids: tuple[str, ...],
        selections: tuple[Selection, ...] = (),
        omissions: tuple[RacialOmission, ...] = (),
    ) -> TemplatePreview:
        draft = CharacterDraft.model_validate(draft)
        templates = self._selected_templates(template_ids)
        supplied = self._selection_map(templates, selections)
        omitted = self._omission_map(templates, omissions)
        contributions = self._contributions(
            draft, templates, supplied, omitted, include_omitted=False
        )
        expanded = _merge(contributions, self._compiler).model_copy(
            update={"name": draft.name, "backstory": draft.backstory}
        )
        purchased = {purchase.definition_id for purchase in expanded.purchases}
        taboo = {identifier for template in templates for identifier in template.taboo_traits}
        if taboo.intersection(purchased):
            raise ValidationError("Template taboo trait is selected")
        compiled = self._compiler.compile(expanded)
        full_contributions = self._contributions(
            draft, templates, supplied, omitted, include_omitted=True
        )
        full = self._compiler.compile(_merge(full_contributions, self._compiler), dry_run=True)
        self._validate_choice_budgets(templates, full_contributions, full)
        applied = [
            AppliedAdjustment(
                id=entry.id,
                template_id=template.id,
                points=entry.points,
                reason=entry.reason,
                counts_as_disadvantage=entry.counts_as_disadvantage,
            )
            for template in templates
            for entry in template.adjustments
        ]
        for template_id, definition_id in sorted(omitted):
            # Removing the racial component changes the compiler total once; the
            # source's "No X" replacement changes it by the same amount again.
            without_one = [
                entry
                for entry in full_contributions
                if not (
                    entry.origin.template_id == template_id
                    and entry.purchase.definition_id == definition_id
                )
            ]
            omitted_compilation = self._compiler.compile(
                _merge(without_one, self._compiler), dry_run=True
            )
            delta = full.spent - omitted_compilation.spent
            applied.append(
                AppliedAdjustment(
                    id=f"omitted:{template_id}:{definition_id}",
                    template_id=template_id,
                    points=-delta,
                    reason=f"Omitted racial trait replacement for {definition_id}",
                    counts_as_disadvantage=True,
                )
            )
        return TemplatePreview(
            expanded,
            tuple(template.id for template in templates),
            self.digest,
            self._apply_adjustments(compiled, tuple(applied)),
            tuple(entry.origin for entry in contributions),
            tuple(applied),
            tuple(Selection.model_validate(entry) for entry in selections),
            tuple(RacialOmission.model_validate(entry) for entry in omissions),
            template_ids,
        )

    def advance(self, prior: TemplatePreview, draft: CharacterDraft) -> TemplatePreview:
        """Recompile a personal advancement without erasing template provenance."""
        if prior.template_digest != self.digest:
            raise ValidationError("Template catalog changed before advancement")
        return self.preview(
            draft,
            prior.root_template_ids,
            prior.selections,
            prior.omissions,
        )


def representative_templates() -> tuple[Template, ...]:
    return (
        Template(id="template:human", kind="racial"),
        Template(
            id="template:low-light-human",
            kind="racial",
            purchases=(
                Purchase(definition_id="trait:night-vision", amount=2),
                Purchase(definition_id="trait:acute-hearing"),
            ),
        ),
        Template(
            id="template:scholar",
            kind="occupational",
            purchases=(Purchase(definition_id="trait:single-minded"),),
            choices=(
                TemplateChoice(
                    id="aptitude",
                    options=(
                        TemplateOption(
                            id="memory",
                            purchases=(Purchase(definition_id="trait:eidetic-memory"),),
                        ),
                        TemplateOption(
                            id="creativity",
                            purchases=(Purchase(definition_id="trait:versatile"),),
                        ),
                    ),
                ),
            ),
        ),
        Template(
            id="template:curious-scholar",
            kind="occupational",
            includes=("template:scholar",),
            purchases=(
                Purchase(definition_id="trait:curious", trait=TraitOptions(self_control=12)),
            ),
        ),
        Template(
            id="template:envoy",
            kind="occupational",
            purchases=(
                Purchase(definition_id="trait:charisma", amount=2),
                Purchase(definition_id="trait:status"),
                Purchase(definition_id="trait:voice"),
                Purchase(
                    definition_id="trait:overconfidence",
                    trait=TraitOptions(self_control=12),
                ),
            ),
            taboo_traits=("trait:shyness-severe",),
        ),
        Template(
            id="template:celebrated-envoy",
            kind="occupational",
            includes=("template:envoy",),
            purchases=(
                Purchase(definition_id="trait:appearance-handsome"),
                Purchase(definition_id="trait:reputation-bravery", amount=2),
            ),
        ),
        Template(
            id="template:guard",
            kind="occupational",
            purchases=(
                Purchase(definition_id="trait:combat-reflexes"),
                Purchase(definition_id="trait:fit"),
                Purchase(definition_id="trait:sense-of-duty-small-group"),
            ),
        ),
    )
