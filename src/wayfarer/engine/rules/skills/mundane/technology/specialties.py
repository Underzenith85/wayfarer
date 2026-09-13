"""Campaign-scoped concrete specialties for open technology skill families (#390)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
)
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.skills.mundane.technology.inventory import (
    PROCEDURES,
    PROFILE,
    RUNTIME_PROCEDURE,
    TECHNOLOGY_LEVEL,
    TechnologyProcedure,
)
from wayfarer.engine.rules.types.skill import (
    CampaignDefaultSelection,
    CampaignSkillSpecialty,
    Specialty,
)
from wayfarer.errors import ValidationError

_SEPARATOR = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class CampaignTechnologySubject:
    """One subject explicitly admitted by campaign setup.

    ``family`` is the catalog selector, while ``subject`` is the setting-owned
    display value.  IDs and mechanical metadata are derived server-side.
    """

    family: str
    subject: str


def _slug(subject: str) -> str:
    value = _SEPARATOR.sub("-", subject.casefold()).strip("-")
    if not value:
        raise ValidationError("Campaign specialty subject needs an identifier")
    return value


@dataclass(frozen=True, slots=True)
class CampaignTechnologySpecialties:
    """Immutable campaign registry for the four Basic Set open families.

    The global source catalog stays untouched.  Each configured campaign gets a
    deterministic set of derived definitions and procedures, and an omitted
    subject therefore has no definition or executable dispatch.
    """

    subjects: tuple[CampaignTechnologySubject, ...] = ()

    def __post_init__(self) -> None:
        identities: set[tuple[str, str]] = set()
        ids: set[str] = set()
        for subject in self.subjects:
            if not isinstance(subject, CampaignTechnologySubject) or not isinstance(
                subject.family, str
            ):
                raise ValidationError("Campaign specialties need typed family subjects")
            parent = PROCEDURES.get(subject.family)
            if parent is None or parent.open_subject is None or parent.task is None:
                raise ValidationError(f"Skill is not an open technology family: {subject.family}")
            if (
                not isinstance(subject.subject, str)
                or not subject.subject
                or subject.subject != subject.subject.strip()
            ):
                raise ValidationError("Campaign specialty subjects must be nonempty and trimmed")
            if len(subject.subject) > 100:
                raise ValidationError("Campaign specialty subject is too long")
            identity = (subject.family, subject.subject.casefold())
            definition_id = f"{subject.family}-{_slug(subject.subject)}"
            if identity in identities or definition_id in ids:
                raise ValidationError(f"Duplicate campaign specialty: {definition_id}")
            identities.add(identity)
            ids.add(definition_id)

    def selections(self) -> tuple[CampaignSkillSpecialty, ...]:
        result = []
        for subject in self.subjects:
            parent = PROCEDURES[subject.family]
            slug = _slug(subject.subject)
            result.append(
                CampaignSkillSpecialty(
                    subject.family,
                    f"{subject.family}-{slug}",
                    f"{parent.name} ({subject.subject})",
                    slug,
                )
            )
        return tuple(result)

    def procedures(self) -> Mapping[str, TechnologyProcedure]:
        result: dict[str, TechnologyProcedure] = {}
        for selection in self.selections():
            parent = PROCEDURES[selection.family]
            assert parent.task is not None
            result[selection.definition_id] = replace(
                parent,
                id=selection.definition_id,
                name=selection.name,
                specialty=Specialty(selection.family.removeprefix("skill:"), selection.specialty),
                task=parent.task,
                specialties=(),
                resolved=(RUNTIME_PROCEDURE, TECHNOLOGY_LEVEL),
                open_subject=None,
                transferred={},
            )
        return MappingProxyType(result)

    def definitions(self, catalog: Mapping[str, RuleDefinition]) -> tuple[RuleDefinition, ...]:
        """Materialize only when the selected package carries the exact family."""
        procedures = self.procedures()
        result = []
        for selection in self.selections():
            parent = PROCEDURES[selection.family]
            definition = catalog.get(selection.family)
            if (
                definition is None
                or definition.id != parent.id
                or definition.kind is not DefinitionKind.SKILL
                or definition.name != parent.name
                or definition.source_id != source(PROFILE).id
                or definition.point_cost is not None
                or definition.status is not ImplementationStatus.UNSUPPORTED
                or definition.prerequisites
                or definition.exclusions
                or definition.parameters
                or definition.hooks
                or definition.skill != replace(parent.spec(), technology_level_required=True)
            ):
                raise ValidationError(
                    f"Campaign specialty requires its exact catalog family: {selection.family}"
                )
            procedure = procedures[selection.definition_id]
            assert procedure.task is not None
            assert definition.skill is not None
            result.append(
                replace(
                    definition,
                    id=selection.definition_id,
                    name=selection.name,
                    status=ImplementationStatus.IMPLEMENTED,
                    hooks=("character.gurps-skill", procedure.task.dispatch.value),
                    skill=replace(
                        definition.skill,
                        specialty=Specialty(
                            selection.family.removeprefix("skill:"), selection.specialty
                        ),
                    ),
                )
            )
        return tuple(result)

    def migrated_families(self, catalog: Mapping[str, RuleDefinition]) -> frozenset[str]:
        """Selectors replaced by this explicit campaign-specialty migration."""
        return frozenset(
            definition_id
            for definition_id in catalog
            if (procedure := PROCEDURES.get(definition_id)) is not None
            and procedure.open_subject is not None
        )

    def default_selections(self) -> frozenset[CampaignDefaultSelection]:
        """Concrete targets for the source's campaign-selected default edges."""
        procedures = self.procedures()
        selections: set[CampaignDefaultSelection] = set()
        biology = [
            p for p in procedures.values() if p.specialty and p.specialty.family == "biology"
        ]
        for origin in biology:
            for target in biology:
                if origin.id != target.id:
                    selections.add(CampaignDefaultSelection(origin.id, "skill:biology", target.id))
        geography = {
            p.specialty.name: p
            for p in procedures.values()
            if p.specialty and p.specialty.family == "geography"
        }
        for origin in procedures.values():
            if origin.specialty and origin.specialty.family == "geology":
                matching_geography = geography.get(origin.specialty.name)
                if matching_geography is not None:
                    selections.add(
                        CampaignDefaultSelection(
                            origin.id, "skill:geography", matching_geography.id
                        )
                    )
        return frozenset(selections)

    def procedure(self, definition_id: str) -> TechnologyProcedure | None:
        return self.procedures().get(definition_id)


def is_open_specialty_id(definition_id: str) -> bool:
    return any(
        definition_id.startswith(family_id + "-")
        for family_id, procedure in PROCEDURES.items()
        if procedure.open_subject is not None
    )
