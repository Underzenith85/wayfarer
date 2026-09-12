"""Versioned, provenance-aware rules packages and campaign policy.

The built-in package contains only Wayfarer prototype definitions. It does not
reproduce or claim complete coverage of any published game system.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.models import RulesPackagePin, RulesReference
from wayfarer.rules.skill_types import SkillSpec
from wayfarer.rules.traits import TraitRules, validate_metadata

VERSION: Final = "wayfarer-lite-1"
BUDGET: Final = 100
ATTR_COST: Final = MappingProxyType({"ST": 10, "DX": 20, "IQ": 20, "HT": 10})
SKILLS: Final = MappingProxyType(
    {
        "Stealth": ("DX", -1),
        "Observation": ("IQ", -1),
        "Diplomacy": ("IQ", -2),
        "Survival": ("IQ", -1),
    }
)
TRAITS: Final = MappingProxyType({"Keen senses": 5, "Fit": 5, "Curious": -5, "Code of honor": -10})


class ImplementationStatus(StrEnum):
    IMPLEMENTED = "implemented"
    MANUAL = "manual-adjudication"
    UNSUPPORTED = "unsupported"


class DefinitionKind(StrEnum):
    ATTRIBUTE = "attribute"
    SECONDARY = "secondary"
    SKILL = "skill"
    TRAIT = "trait"
    EQUIPMENT = "equipment"


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Rights/provenance metadata, never copied rules text."""

    id: str
    title: str
    rights: Literal["original", "licensed", "user-supplied-reference"]
    citation: str | None = None


@dataclass(frozen=True, slots=True)
class RuleDefinition:
    id: str
    kind: DefinitionKind
    name: str
    source_id: str
    point_cost: int | None
    status: ImplementationStatus
    prerequisites: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    hooks: tuple[str, ...] = ()
    skill: SkillSpec | None = None
    trait_rules: TraitRules | None = None


@dataclass(frozen=True, slots=True)
class RulesPackage:
    id: str
    version: str
    edition: str
    sources: tuple[SourceReference, ...]
    definitions: tuple[RuleDefinition, ...]
    dependencies: tuple[str, ...] = ()

    def canonical_json(self) -> str:
        """Stable package representation shared by pins and source approval gates."""
        data = asdict(self)
        # Absent skill and trait metadata must not change historic package pins.
        for definition in data["definitions"]:
            for extension in ("skill", "trait_rules"):
                if definition[extension] is None:
                    del definition[extension]
            # A later skill mechanic that a definition does not use is absent for
            # the same reason: an unused alternative-prerequisite set must not
            # move the digest of a package pinned before the shape existed.
            skill = definition.get("skill")
            if skill is not None:
                if not skill["prerequisite_groups"]:
                    del skill["prerequisite_groups"]
                if not skill["technology_level_required"]:
                    del skill["technology_level_required"]
                prerequisites = list(skill["prerequisites"])
                prerequisites.extend(
                    prerequisite
                    for group in skill.get("prerequisite_groups", ())
                    for prerequisite in group["alternatives"]
                )
                for prerequisite in prerequisites:
                    if prerequisite["kind"] == "trained-skill":
                        del prerequisite["kind"]
                    if prerequisite["minimum_technology_level"] is None:
                        del prerequisite["minimum_technology_level"]
                # Conditional defaults were added after the first package pins.
                # Absence remains absence rather than changing every historic digest.
                for default in skill["defaults"]:
                    if not default["conditions"]:
                        del default["conditions"]
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PackagePin:
    id: str
    version: str
    digest: str


@dataclass(frozen=True, slots=True)
class CampaignPolicy:
    id: str
    version: int
    point_budget: int
    disadvantage_limit: int
    attribute_ceiling: int
    skill_ceiling: int
    permitted_sources: frozenset[str]
    technology_level: int | None = None
    allow_supernatural: bool = False
    allowed_equipment: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CampaignRules:
    edition: str
    packages: tuple[PackagePin, ...]
    policy_id: str
    policy_version: int


class RulesCatalog:
    """Immutable validated package registry."""

    def __init__(self, packages: tuple[RulesPackage, ...]) -> None:
        self._packages = {(p.id, p.version): p for p in packages}
        if len(self._packages) != len(packages):
            raise ValidationError("Duplicate rules package ID and version")
        self._validate(packages)

    @staticmethod
    def _validate(packages: tuple[RulesPackage, ...]) -> None:
        available_packages = {p.id for p in packages}
        for package in packages:
            if any(dep not in available_packages for dep in package.dependencies):
                raise ValidationError(f"Package {package.id} has a missing dependency")
            sources = {source.id for source in package.sources}
            definitions = {definition.id for definition in package.definitions}
            if len(definitions) != len(package.definitions):
                raise ValidationError(f"Package {package.id} has duplicate definition IDs")
            for definition in package.definitions:
                if definition.trait_rules is not None:
                    if definition.kind is not DefinitionKind.TRAIT:
                        raise ValidationError("Trait metadata requires a trait definition")
                    validate_metadata(definition.trait_rules)
                    if definition.parameters and set(definition.parameters) != {
                        p.name for p in definition.trait_rules.parameters
                    }:
                        raise ValidationError("Trait parameter declarations disagree")
                if definition.source_id not in sources:
                    raise ValidationError(f"Definition {definition.id} has a missing source")
                refs = (*definition.prerequisites, *definition.exclusions)
                if any(ref not in definitions for ref in refs):
                    raise ValidationError(f"Definition {definition.id} has a missing reference")
            graph = {d.id: set(d.prerequisites) for d in package.definitions}
            try:
                tuple(TopologicalSorter(graph).static_order())
            except CycleError as exc:
                raise ValidationError(f"Package {package.id} has a prerequisite cycle") from exc

    def package(self, pin: PackagePin) -> RulesPackage:
        package = self._packages.get((pin.id, pin.version))
        if package is None or package.digest != pin.digest:
            raise ValidationError(f"Rules package pin does not resolve: {pin.id}@{pin.version}")
        return package

    def activate(self, rules: CampaignRules, policy: CampaignPolicy) -> None:
        if (rules.policy_id, rules.policy_version) != (policy.id, policy.version):
            raise ValidationError("Campaign policy pin does not resolve")
        for pin in rules.packages:
            package = self.package(pin)
            if package.edition != rules.edition:
                raise ValidationError("Rules package edition mismatch")
            for definition in package.definitions:
                if definition.source_id not in policy.permitted_sources:
                    raise ValidationError(f"Source not permitted: {definition.source_id}")
                if definition.status is ImplementationStatus.UNSUPPORTED:
                    raise ValidationError(
                        f"Unsupported definition cannot activate: {definition.id}"
                    )


PROTOTYPE_SOURCE = SourceReference(
    id="source:wayfarer-original", title="Wayfarer prototype rules", rights="original"
)
PROTOTYPE_PACKAGE = RulesPackage(
    id="package:wayfarer-lite",
    version="1.0.0",
    edition="wayfarer-lite",
    sources=(PROTOTYPE_SOURCE,),
    definitions=tuple(
        RuleDefinition(
            id=f"attribute:{name.lower()}",
            kind=DefinitionKind.ATTRIBUTE,
            name=name,
            source_id=PROTOTYPE_SOURCE.id,
            point_cost=cost,
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.attribute",),
        )
        for name, cost in ATTR_COST.items()
    )
    + tuple(
        RuleDefinition(
            id=f"skill:{name.lower()}",
            kind=DefinitionKind.SKILL,
            name=name,
            source_id=PROTOTYPE_SOURCE.id,
            point_cost=None,
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.skill", "check.target"),
        )
        for name in SKILLS
    )
    + tuple(
        RuleDefinition(
            id=f"trait:{name.lower().replace(' ', '-')}",
            kind=DefinitionKind.TRAIT,
            name=name,
            source_id=PROTOTYPE_SOURCE.id,
            point_cost=cost,
            status=ImplementationStatus.MANUAL,
        )
        for name, cost in TRAITS.items()
    ),
)
DEFAULT_POLICY = CampaignPolicy(
    id="policy:wayfarer-demo",
    version=1,
    point_budget=BUDGET,
    disadvantage_limit=25,
    attribute_ceiling=14,
    skill_ceiling=16,
    permitted_sources=frozenset({PROTOTYPE_SOURCE.id}),
)
DEFAULT_RULES = CampaignRules(
    edition=PROTOTYPE_PACKAGE.edition,
    packages=(
        PackagePin(
            id=PROTOTYPE_PACKAGE.id,
            version=PROTOTYPE_PACKAGE.version,
            digest=PROTOTYPE_PACKAGE.digest,
        ),
    ),
    policy_id=DEFAULT_POLICY.id,
    policy_version=DEFAULT_POLICY.version,
)
DEFAULT_CATALOG = RulesCatalog((PROTOTYPE_PACKAGE,))


def reference(rules: CampaignRules) -> RulesReference:
    return RulesReference(
        edition=rules.edition,
        packages=[
            RulesPackagePin(id=pin.id, version=pin.version, digest=pin.digest)
            for pin in rules.packages
        ],
        policy_id=rules.policy_id,
        policy_version=rules.policy_version,
    )
