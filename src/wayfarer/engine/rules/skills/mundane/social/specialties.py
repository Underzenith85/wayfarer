"""Campaign-scoped Savoir-Faire milieu specialties (#366)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.skills.mundane.social.inventory import (
    DISPATCH,
    PROCEDURES,
    PROFILE,
    RUNTIME_PROCEDURE,
    SocialProcedure,
)
from wayfarer.engine.rules.types.skill import (
    CampaignDefaultSelection,
    CampaignSkillSpecialty,
    Specialty,
)
from wayfarer.errors import ValidationError

_SEPARATOR = re.compile(r"[^a-z0-9]+")
FAMILY = "skill:savoir-faire"


@dataclass(frozen=True, slots=True)
class CampaignSocialSubject:
    """One social milieu explicitly admitted by campaign setup."""

    family: str
    subject: str


def _slug(subject: str) -> str:
    value = _SEPARATOR.sub("-", subject.casefold()).strip("-")
    if not value:
        raise ValidationError("Campaign social specialty subject needs an identifier")
    return value


@dataclass(frozen=True, slots=True)
class CampaignSocialSpecialties:
    """Immutable campaign registry for B218's open social-milieu family."""

    subjects: tuple[CampaignSocialSubject, ...] = ()

    def __post_init__(self) -> None:
        identities: set[tuple[str, str]] = set()
        ids: set[str] = set()
        for subject in self.subjects:
            if not isinstance(subject, CampaignSocialSubject) or not isinstance(
                subject.family, str
            ):
                raise ValidationError("Campaign social specialties need typed family subjects")
            parent = PROCEDURES.get(subject.family)
            if parent is None or parent.open_subject is None:
                raise ValidationError(f"Skill is not an open social family: {subject.family}")
            if (
                not isinstance(subject.subject, str)
                or not subject.subject
                or subject.subject != subject.subject.strip()
            ):
                raise ValidationError(
                    "Campaign social specialty subjects must be nonempty and trimmed"
                )
            if len(subject.subject) > 100:
                raise ValidationError("Campaign social specialty subject is too long")
            identity = (subject.family, subject.subject.casefold())
            definition_id = f"{subject.family}-{_slug(subject.subject)}"
            if identity in identities or definition_id in ids:
                raise ValidationError(f"Duplicate campaign social specialty: {definition_id}")
            identities.add(identity)
            ids.add(definition_id)

    def selections(self) -> tuple[CampaignSkillSpecialty, ...]:
        return tuple(
            CampaignSkillSpecialty(
                subject.family,
                f"{subject.family}-{_slug(subject.subject)}",
                f"{PROCEDURES[subject.family].name} ({subject.subject})",
                _slug(subject.subject),
            )
            for subject in self.subjects
        )

    def procedures(self) -> Mapping[str, SocialProcedure]:
        return MappingProxyType(
            {
                selection.definition_id: replace(
                    PROCEDURES[selection.family],
                    id=selection.definition_id,
                    name=selection.name,
                    specialty=Specialty(
                        selection.family.removeprefix("skill:"), selection.specialty
                    ),
                    specialties=(),
                    open_subject=None,
                    resolved=(RUNTIME_PROCEDURE,),
                    transferred={},
                )
                for selection in self.selections()
            }
        )

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
                or definition.skill != parent.spec()
            ):
                raise ValidationError(
                    f"Campaign social specialty requires its exact catalog family: "
                    f"{selection.family}"
                )
            procedure = procedures[selection.definition_id]
            assert definition.skill is not None and procedure.specialty is not None
            result.append(
                replace(
                    definition,
                    id=selection.definition_id,
                    name=selection.name,
                    status=ImplementationStatus.IMPLEMENTED,
                    hooks=("character.gurps-skill", DISPATCH),
                    skill=replace(definition.skill, specialty=procedure.specialty),
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
        return frozenset()

    def procedure(self, definition_id: str) -> SocialProcedure | None:
        return self.procedures().get(definition_id)


def is_open_specialty_id(definition_id: str) -> bool:
    return definition_id.startswith(FAMILY + "-") and len(definition_id) > len(FAMILY) + 1
