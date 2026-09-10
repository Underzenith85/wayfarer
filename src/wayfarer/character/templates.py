"""Typed template expansion into the existing authoritative character compiler.

No template supplies prices, formulas, activation receipts, or a second compiler.
The selected examples are original compositions using B258-261 construction,
not reproductions of the sample occupational or nonhuman racial templates.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Compilation, Purchase
from wayfarer.errors import ValidationError
from wayfarer.rules.mundane_traits import PROFILE
from wayfarer.rules.traits import TraitOptions


class TemplateOption(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    id: str = Field(min_length=1, max_length=100)
    purchases: tuple[Purchase, ...] = Field(strict=False, min_length=1, max_length=100)


class TemplateChoice(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    id: str = Field(min_length=1, max_length=100)
    count: int = Field(default=1, ge=1, le=100)
    options: tuple[TemplateOption, ...] = Field(strict=False, min_length=1, max_length=100)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("Duplicate template option")
        if self.count > len(self.options):
            raise ValueError("Template choice exceeds its available options")
        return self


class Template(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["racial", "occupational"]
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    purchases: tuple[Purchase, ...] = Field(default=(), strict=False, max_length=100)
    includes: tuple[str, ...] = Field(default=(), strict=False, max_length=20)
    choices: tuple[TemplateChoice, ...] = Field(default=(), strict=False, max_length=20)
    taboo_traits: tuple[str, ...] = Field(default=(), strict=False, max_length=100)
    reference: str = "B258-261; original representative construction"

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.includes)) != len(self.includes):
            raise ValueError("Duplicate included template")
        if len({choice.id for choice in self.choices}) != len(self.choices):
            raise ValueError("Duplicate template choice")
        if self.kind == "racial" and self.choices:
            raise ValueError("Racial template traits cannot be optional")
        return self


class Selection(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    template_id: str
    choice_id: str
    option_ids: tuple[str, ...] = Field(strict=False, min_length=1, max_length=100)


@dataclass(frozen=True, slots=True)
class TemplatePreview:
    draft: CharacterDraft
    template_ids: tuple[str, ...]
    template_digest: str
    compilation: Compilation


class TemplateCatalog:
    """Trusted template definitions bound to one compiler's exact package pins.

    Duplicate purchases are never silently accumulated or overwritten. Racial
    attribute stacking is deliberately unsupported: callers must adjudicate a
    new trusted template rather than inventing arithmetic in a client request.
    """

    def __init__(self, templates: tuple[Template, ...], compiler: CharacterCompiler) -> None:
        if compiler.statistics_profile != PROFILE:
            raise ValidationError("Templates require the exact Basic Set profile")
        checked = tuple(Template.model_validate(template) for template in templates)
        self._templates = {template.id: template for template in checked}
        self._compiler = compiler
        if len(self._templates) != len(checked):
            raise ValidationError("Duplicate template identifier")
        for template in checked:
            if not set(template.includes) <= self._templates.keys():
                raise ValidationError("Unresolved included template")
            purchases = (
                *template.purchases,
                *(p for c in template.choices for o in c.options for p in o.purchases),
            )
            references = {p.definition_id for p in purchases} | set(template.taboo_traits)
            if not references <= compiler.definitions.keys():
                raise ValidationError("Unresolved template purchase or taboo trait")
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
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

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

    def preview(
        self,
        draft: CharacterDraft,
        template_ids: tuple[str, ...],
        selections: tuple[Selection, ...] = (),
    ) -> TemplatePreview:
        draft = CharacterDraft.model_validate(draft)
        if not template_ids:
            raise ValidationError("Select at least one template")
        templates: list[Template] = []
        seen: set[str] = set()
        for identifier in template_ids:
            templates.extend(self._expand(identifier, (), seen))
        supplied: dict[tuple[str, str], tuple[str, ...]] = {}
        for raw in selections:
            selection = Selection.model_validate(raw)
            key = (selection.template_id, selection.choice_id)
            if key in supplied:
                raise ValidationError("Duplicate template selection")
            supplied[key] = selection.option_ids
        required = {(t.id, c.id) for t in templates for c in t.choices}
        if supplied.keys() != required:
            raise ValidationError("Missing or unexpected template selections")
        purchases = list(draft.purchases)
        for template in templates:
            purchases.extend(template.purchases)
            for choice in template.choices:
                chosen = supplied[(template.id, choice.id)]
                options = {option.id: option for option in choice.options}
                if (
                    len(chosen) != choice.count
                    or len(set(chosen)) != len(chosen)
                    or not set(chosen) <= options.keys()
                ):
                    raise ValidationError("Invalid template choice")
                for identifier in sorted(chosen):
                    purchases.extend(options[identifier].purchases)
        purchased = [purchase.definition_id for purchase in purchases]
        if len(purchased) != len(set(purchased)):
            raise ValidationError("Conflicting template purchases require explicit reconciliation")
        taboo = {identifier for template in templates for identifier in template.taboo_traits}
        if taboo.intersection(purchased):
            raise ValidationError("Template taboo trait is selected")
        expanded = CharacterDraft(
            name=draft.name, backstory=draft.backstory, purchases=tuple(purchases)
        )
        return TemplatePreview(
            expanded, tuple(t.id for t in templates), self.digest, self._compiler.compile(expanded)
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
                            id="memory", purchases=(Purchase(definition_id="trait:eidetic-memory"),)
                        ),
                        TemplateOption(
                            id="creativity", purchases=(Purchase(definition_id="trait:versatile"),)
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
        # Composed only of traits with executable runtime bindings, so an
        # approved build of this template can reach the social services.
        Template(
            id="template:envoy",
            kind="occupational",
            purchases=(
                Purchase(definition_id="trait:charisma", amount=2),
                Purchase(definition_id="trait:status"),
                Purchase(definition_id="trait:voice"),
                Purchase(definition_id="trait:overconfidence", trait=TraitOptions(self_control=12)),
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
