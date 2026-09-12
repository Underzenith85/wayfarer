"""Authored enchanting recipes and restart-safe project records (B480-482)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

EnchantmentMethod = Literal["quick-and-dirty", "slow-and-sure"]


class EnchantmentMaterial(Record):
    definition_id: Id
    quantity: int = Field(ge=1)


class EnchantmentRecipe(Record):
    """GM-authored item recipe; values are data, never inferred spell formulas."""

    id: Id
    spell_id: Id
    effect_id: Id
    method: EnchantmentMethod
    energy_required: int = Field(ge=1)
    target_definition_ids: tuple[Id, ...] = Field(min_length=1)
    workspace_definition_id: Id
    materials: tuple[EnchantmentMaterial, ...] = ()
    mana: ManaLevel = "normal"
    runtime_spell_id: Literal["light", "daze", "fireball", "create-fire"] | None = None
    runtime_family: str | None = None
    activation: Literal["cast", "always-on"] = "cast"
    requires_magery: bool = False
    power_reduction: int = Field(default=0, ge=0, le=100)
    maximum_charges: int | None = Field(default=None, ge=1)
    maintenance_energy: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def explicit_runtime(self) -> EnchantmentRecipe:
        if (self.runtime_spell_id is None) != (self.runtime_family is None):
            raise ValueError("Executable enchantments require spell and runtime-family bindings")
        if len(set(self.target_definition_ids)) != len(self.target_definition_ids):
            raise ValueError("Duplicate enchantment target definition")
        return self


class MagicItemOffer(Record):
    """Campaign economy data; Basic Set examples are not a global market."""

    id: Id
    recipe_id: Id
    price: int = Field(ge=0)
    availability: Literal["common", "restricted", "rare", "unavailable"]
    market_id: Id


class EnchantingRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    recipes: tuple[EnchantmentRecipe, ...] = ()
    offers: tuple[MagicItemOffer, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> EnchantingRules:
        if len({r.id for r in self.recipes}) != len(self.recipes):
            raise ValueError("Duplicate enchantment recipe")
        if len({o.id for o in self.offers}) != len(self.offers):
            raise ValueError("Duplicate magic-item offer")
        recipes = {r.id for r in self.recipes}
        if any(o.recipe_id not in recipes for o in self.offers):
            raise ValueError("Magic-item offer requires an authored recipe")
        return self


class EnergyContribution(Record):
    actor_id: Id
    fp: int = Field(default=0, ge=0)
    hp: int = Field(default=0, ge=0)

    @property
    def energy(self) -> int:
        return self.fp + self.hp


class EnchantmentWork(Record):
    id: Id
    start: int = Field(ge=0)
    due: int = Field(gt=0)
    enchanter_ids: tuple[Id, ...] = Field(min_length=1)
    contributions: tuple[EnergyContribution, ...] = ()

    @model_validator(mode="after")
    def ordered(self) -> EnchantmentWork:
        if self.due <= self.start or len(set(self.enchanter_ids)) != len(self.enchanter_ids):
            raise ValueError("Invalid enchanting work schedule or duplicate enchanter")
        if {c.actor_id for c in self.contributions} - set(self.enchanter_ids):
            raise ValueError("Energy contribution requires a project enchanter")
        return self


class EnchantmentInterruption(Record):
    command_id: Id
    at: int = Field(ge=0)
    credited_energy: int = Field(ge=0)
    lost_seconds: int = Field(ge=0)


class EnchantmentProject(Record):
    id: Id
    owner_id: Id
    recipe_id: Id
    target_item_id: Id
    enchanter_ids: tuple[Id, ...] = Field(min_length=1)
    status: Literal["active", "interrupted", "abandoned", "completed", "failed"] = "active"
    energy_completed: int = Field(default=0, ge=0)
    delay_seconds: int = Field(default=0, ge=0)
    materials_spent: tuple[EnchantmentMaterial, ...] = ()
    active_work: EnchantmentWork | None = None
    interruptions: tuple[EnchantmentInterruption, ...] = ()
    check: CheckTrace | None = None
    magic_item_binding_id: Id | None = None


def busy_actor_ids(projects: tuple[EnchantmentProject, ...]) -> frozenset[str]:
    return frozenset(
        actor
        for project in projects
        if project.status == "active" and project.active_work is not None
        for actor in project.enchanter_ids
    )


def validate_projects(
    projects: tuple[EnchantmentProject, ...],
    rules: EnchantingRules | None,
    game_time: int,
    *,
    item_ids: frozenset[str] | None = None,
    binding_ids: frozenset[str] | None = None,
) -> None:
    if len({p.id for p in projects}) != len(projects):
        raise ValidationError("Duplicate enchantment project")
    if projects and rules is None:
        raise ValidationError("Enchantment state requires authored enchanting rules")
    recipes = {r.id: r for r in rules.recipes} if rules else {}
    for project in projects:
        recipe = recipes.get(project.recipe_id)
        if recipe is None:
            raise ValidationError("Enchantment project has no authored recipe")
        if project.energy_completed > recipe.energy_required:
            raise ValidationError("Enchantment progress exceeds required energy")
        if (
            item_ids is not None
            and project.status != "failed"
            and project.target_item_id not in item_ids
        ):
            raise ValidationError("Enchantment target item is missing")
        if project.active_work and (
            project.status != "active" or project.active_work.start > game_time
        ):
            raise ValidationError("Enchanting work must be active and already started")
        if project.status == "completed" and (
            project.energy_completed != recipe.energy_required
            or project.magic_item_binding_id is None
        ):
            raise ValidationError("Completed enchantment requires full energy and an item binding")
        if (
            project.status == "completed"
            and binding_ids is not None
            and project.magic_item_binding_id not in binding_ids
        ):
            raise ValidationError("Completed enchantment binding is missing from its item")
