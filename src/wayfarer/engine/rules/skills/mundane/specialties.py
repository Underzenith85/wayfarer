"""Composable campaign-scoped mundane skill specialty registries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.types.skill import CampaignDefaultSelection
from wayfarer.errors import ValidationError


class CampaignSpecialtyRegistry(Protocol):
    """Structural contract consumed by character compilation."""

    def definitions(self, catalog: Mapping[str, RuleDefinition]) -> tuple[RuleDefinition, ...]: ...

    def migrated_families(self, catalog: Mapping[str, RuleDefinition]) -> frozenset[str]: ...

    def default_selections(self) -> frozenset[CampaignDefaultSelection]: ...


@dataclass(frozen=True, slots=True)
class CampaignSkillSpecialties:
    """Compose independent specialty families without merging their authorities."""

    registries: tuple[CampaignSpecialtyRegistry, ...] = ()

    def __post_init__(self) -> None:
        if len({type(registry) for registry in self.registries}) != len(self.registries):
            raise ValidationError("Duplicate campaign specialty registry")

    def definitions(self, catalog: Mapping[str, RuleDefinition]) -> tuple[RuleDefinition, ...]:
        result = tuple(
            definition
            for registry in self.registries
            for definition in registry.definitions(catalog)
        )
        if len({definition.id for definition in result}) != len(result):
            raise ValidationError("Campaign specialty registries produce duplicate definitions")
        return result

    def migrated_families(self, catalog: Mapping[str, RuleDefinition]) -> frozenset[str]:
        return frozenset(
            family for registry in self.registries for family in registry.migrated_families(catalog)
        )

    def default_selections(self) -> frozenset[CampaignDefaultSelection]:
        selections = tuple(
            selection for registry in self.registries for selection in registry.default_selections()
        )
        if len(set(selections)) != len(selections):
            raise ValidationError("Campaign specialty registries produce duplicate defaults")
        return frozenset(selections)

    def registry[T: CampaignSpecialtyRegistry](self, kind: type[T]) -> T | None:
        return next(
            (registry for registry in self.registries if isinstance(registry, kind)),
            None,
        )
