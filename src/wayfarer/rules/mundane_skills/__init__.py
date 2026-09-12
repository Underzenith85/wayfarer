"""Dedicated Basic Set mundane skill inventory (#112).

Source-indexed metadata uses the selected Characters third-printing baseline.
Item-level mechanics blockers remain explicit audit data.
No existing package pin is changed and unimplemented runtime skills fail closed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from pydantic import TypeAdapter

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesPackage,
)
from wayfarer.rules.gurps_characters import source
from wayfarer.rules.mundane_skills.ranged import PROCEDURES as RANGED_PROCEDURES
from wayfarer.rules.mundane_skills.ranged import ranged_scope
from wayfarer.rules.mundane_skills.schema import (
    ContextualPrerequisiteRecord,
    Exclusion,
    Exclusions,
    InventoryRow,
    SourceIndex,
)
from wayfarer.rules.mundane_skills.social import PROCEDURES as SOCIAL_PROCEDURES
from wayfarer.rules.mundane_skills.social import unsupported_scope as social_scope
from wayfarer.rules.mundane_skills.technology import PROCEDURES as TECHNOLOGY_PROCEDURES
from wayfarer.rules.mundane_skills.technology import unsupported_scope as technology_scope
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import (
    DefaultCondition,
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
    Specialty,
    Technique,
    TechniqueTemplate,
    VariableFamily,
)

# Prerequisites recorded here whose target another catalog owns (B182).
CROSS_PACKAGE = frozenset({"skill:computer-hacking"})
PROFILE = "gurps-basic-set-4e-2004"
SOURCE = source(PROFILE)
OWNER = 112
CONTEXT_OWNER = 336
# #336 completed the contextual shapes and split what still needs campaign state
# into concrete children. Each remaining blocker names
# the one that owns it instead of resolving into the context owner.
CONTEXT_RESIDUALS = MappingProxyType(
    {
        "conditional-or-skill-defaults": (383,),
        "prerequisite-procedure": (383,),
        "weapon-default-audit": (383,),
        "technology-level-context": (384,),
        "optional-rule-selection": (384,),
        "specialty-expansion": (385,),
        "technique-expansion": (385,),
    }
)
# One runtime binding per procedure group. A row bound by two groups would let
# either one claim it, so overlap is rejected rather than resolved by order.
BINDINGS = (RANGED_PROCEDURES, SOCIAL_PROCEDURES, TECHNOLOGY_PROCEDURES)


class StructuralClass(StrEnum):
    """Audit shapes an inventory row can take; every class must be sampled."""

    LISTING_ONLY = "listing-only"
    ATTRIBUTE_DEFAULT = "attribute-default"
    SKILL_DEFAULT = "skill-default"
    NO_DEFAULT = "no-default"
    PREREQUISITE = "prerequisite"
    OPTIONAL_SPECIALTY = "optional-specialty"
    REQUIRED_SPECIALTY = "required-specialty"
    UNEXPANDED_SPECIALTY = "unexpanded-specialty"
    TECHNIQUE = "technique"
    TECHNOLOGY_LEVEL = "technology-level"
    ALIAS = "alias"
    TECHNIQUE_TEMPLATE = "technique-template"
    VARIABLE_FAMILY = "variable-family"
    ALTERNATIVE_PREREQUISITE = "alternative-prerequisite"


@dataclass(frozen=True)
class SkillAudit:
    id: str
    name: str
    reference: str
    definition: RuleDefinition | None
    blockers: tuple[str, ...]
    followup_issues: tuple[int, ...]
    specialty_required: bool = False
    tl_required: bool = False
    alias_of: str | None = None
    procedure_owner: int = 0
    # B230-233 technique listing and open specialty family metadata (#336). A row
    # carrying either records its contextual shape rather than a missing definition.
    template: TechniqueTemplate | None = None
    variable: VariableFamily | None = None
    # A row is bound only when a runtime module executes it through a service
    # that already resolves it; recording mechanics never sets this. A family
    # row is bound by its concrete specialties and has no dispatch of its own.
    bound: bool = False
    dispatch: str | None = None
    # Blockers this row's procedure owner transferred, each naming the concrete
    # open child that must resolve it.
    transferred: tuple[tuple[str, tuple[int, ...]], ...] = ()
    provenance: str = "Characters Fourth Edition, third printing (February 2008)"

    @property
    def available(self) -> bool:
        return (
            not self.blockers
            and self.definition is not None
            and self.definition.status is ImplementationStatus.IMPLEMENTED
        )

    @property
    def implementation(self) -> str:
        """Certification state of this row, never a family-level claim."""
        if self.definition is None:
            # A technique template and an open family are not rollable skills, so
            # they record their contextual shape instead of a definition. That is
            # not the same as a row whose mechanics are simply missing.
            if self.template is not None or self.variable is not None:
                return "contextual"
            return "listing-only"
        return "implemented" if self.bound else "unsupported"

    @property
    def owners(self) -> tuple[int, ...]:
        """Named mechanics issues other than this inventory's own accounting."""
        transferred = tuple(i for _, owners in self.transferred for i in owners)
        return tuple(
            dict.fromkeys(((self.procedure_owner,) if self.procedure_owner else ()) + transferred)
        )

    @property
    def blocker_owners(self) -> dict[str, tuple[int, ...]]:
        """Every blocker has an explicit live follow-up, including source review.

        A procedure owner that split a blocker into a bounded child adds that
        child here, so a transfer stays visible instead of resolving into
        silence under the owner that handed it on.
        """
        transferred = dict(self.transferred)
        return {
            blocker: tuple(
                dict.fromkeys(
                    (
                        (self.procedure_owner,)
                        if blocker in ("runtime-procedure", "combat-procedure")
                        else CONTEXT_RESIDUALS.get(blocker, (CONTEXT_OWNER,))
                    )
                    + transferred.get(blocker, ())
                )
            )
            for blocker in self.blockers
        }

    @property
    def structural_classes(self) -> tuple[StructuralClass, ...]:
        """Classify recorded structure only; absent metadata is never inferred."""
        spec = self.definition.skill if self.definition else None
        found = set()
        if spec is None:
            found.add(StructuralClass.LISTING_ONLY)
        else:
            if not spec.defaults:
                found.add(StructuralClass.NO_DEFAULT)
            if any(default.target in A for default in spec.defaults):
                found.add(StructuralClass.ATTRIBUTE_DEFAULT)
            if any(default.target not in A for default in spec.defaults):
                found.add(StructuralClass.SKILL_DEFAULT)
            if spec.prerequisites:
                found.add(StructuralClass.PREREQUISITE)
            if spec.specialty is not None:
                found.add(
                    StructuralClass.OPTIONAL_SPECIALTY
                    if spec.specialty.optional_parent
                    else StructuralClass.REQUIRED_SPECIALTY
                )
            if spec.technique is not None:
                found.add(StructuralClass.TECHNIQUE)
        if self.specialty_required and (spec is None or spec.specialty is None):
            found.add(StructuralClass.UNEXPANDED_SPECIALTY)
        if self.tl_required:
            found.add(StructuralClass.TECHNOLOGY_LEVEL)
        if self.alias_of:
            found.add(StructuralClass.ALIAS)
        if self.template is not None or "technique-expansion" in self.blockers:
            found.add(StructuralClass.TECHNIQUE_TEMPLATE)
        if self.variable is not None:
            found.add(StructuralClass.VARIABLE_FAMILY)
        if spec is not None and spec.prerequisite_groups:
            found.add(StructuralClass.ALTERNATIVE_PREREQUISITE)
        return tuple(sorted(found))


def source_inventory() -> tuple[InventoryRow, ...]:
    return TypeAdapter(tuple[InventoryRow, ...]).validate_json(
        Path(__file__).with_name("inventory.json").read_text()
    )


def source_index() -> SourceIndex:
    """Independent B301-B304 index plus explicitly identified chapter expansions."""
    return SourceIndex.model_validate_json(
        Path(__file__).with_name("source_index.json").read_text()
    )


def validate_source_index(entries: tuple[SkillAudit, ...], excluded: tuple[Exclusion, ...]) -> None:
    index = source_index()
    identifiers = {row.id for row in index.entries}
    if len(identifiers) != len(index.entries):
        raise ValidationError("Duplicate source index entry")
    included = {entry.id.removeprefix("skill:") for entry in entries}
    transferred = {entry.id for entry in excluded}
    if included & transferred:
        raise ValidationError("Source entry is both included and transferred")
    accounted = included | transferred
    indexed = {target for row in index.entries for target in row.targets}
    if accounted != indexed:
        raise ValidationError(
            f"Source index accounting mismatch: missing={sorted(indexed - accounted)}, "
            f"unindexed={sorted(accounted - indexed)}"
        )
    references = {entry.id.removeprefix("skill:"): entry.reference for entry in entries}
    references.update({entry.id: f"B{entry.page}" for entry in excluded})
    for row in index.entries:
        if row.parent and (row.parent not in identifiers or row.parent == row.id):
            raise ValidationError(f"Invalid source expansion parent: {row.id}")
        if any(references[target] != f"B{row.page}" for target in row.targets):
            raise ValidationError(f"Source index page mismatch: {row.id}")


def exclusions() -> tuple[Exclusion, ...]:
    rows = Exclusions.model_validate_json(Path(__file__).with_name("exclusions.json").read_text())
    names = [e.name.casefold() for e in rows.excluded]
    included = {row.name.casefold() for row in source_inventory()}
    if len(set(names)) != len(names) or included.intersection(names):
        raise ValidationError("Duplicate or overlapping excluded skills")
    return rows.excluded


def transferred_exclusions() -> tuple[Exclusion, ...]:
    """Reuse the owning supernatural catalog as the authority for excluded skills.

    An excluded skill is only accounted for while another inventory carries it
    with the same page and the same named follow-up issues. Drift there is a
    coverage failure here, not a silent removal from the Basic Set chapter.
    """
    from wayfarer.rules.supernatural import inventory as owning_catalog

    owned = {entry.id: entry for entry in owning_catalog().entries if entry.kind == "skill"}
    rows = exclusions()
    if len(owned) != len(rows):
        raise ValidationError("Excluded skills and their owning catalog differ")
    for row in rows:
        entry = owned.get(f"skill:{row.id}")
        if entry is None or entry.name != row.name or entry.page != row.page:
            raise ValidationError(f"Excluded skill is not carried by its owner: {row.name}")
        if entry.blockers != row.owners:
            raise ValidationError(f"Excluded skill owner drift: {row.name}")
    return rows


def coverage_blockers(profile_id: str) -> tuple[int, ...]:
    """Aggregate item-level owners for certification and authoring reports."""
    if profile_id != PROFILE:
        raise ValidationError("Mundane skill inventory is outside the selected profile")
    entries = inventory()
    return tuple(sorted({issue for entry in entries for issue in entry.followup_issues}))


def inventory() -> tuple[SkillAudit, ...]:
    rows = source_inventory()
    result = []

    def prerequisite(value: str | ContextualPrerequisiteRecord) -> SkillPrerequisite:
        if isinstance(value, str):
            return SkillPrerequisite(f"skill:{value}")
        kind = value.kind
        target = f"skill:{value.target}" if kind is PrerequisiteKind.TRAINED_SKILL else value.target
        return SkillPrerequisite(
            target,
            value.minimum,
            kind,
            value.minimum_technology_level,
        )

    for row in rows:
        identifier = "skill:" + row.id
        definition = None
        attributes = {
            "IQ": A.IQ,
            "DX": A.DX,
            "HT": A.HT,
            "ST": A.ST,
            "Will": A.WILL,
            "Per": A.PER,
            "Perception": A.PER,
        }
        spec = (
            SkillSpec(
                attributes[row.attribute],
                row.difficulty,
                f"B{row.page}",
                tuple(
                    SkillDefault(
                        attributes[d.attribute],
                        d.modifier,
                        tuple(DefaultCondition(c.kind, c.value) for c in d.conditions),
                    )
                    for d in row.attribute_defaults
                )
                + tuple(
                    SkillDefault(
                        f"skill:{d.target}",
                        d.modifier,
                        tuple(DefaultCondition(c.kind, c.value) for c in d.conditions),
                    )
                    for d in row.skill_defaults
                ),
                tuple(SkillPrerequisite(f"skill:{p}") for p in row.prerequisites)
                + tuple(prerequisite(p) for p in row.contextual_prerequisites),
                Specialty(
                    row.specialty.family,
                    row.specialty.name,
                    f"skill:{row.specialty.optional_parent}"
                    if row.specialty.optional_parent
                    else None,
                )
                if row.specialty
                else None,
                Technique(
                    f"skill:{row.technique.parent}",
                    row.technique.default_modifier,
                    row.technique.maximum_modifier,
                )
                if row.technique
                else None,
                tuple(
                    PrerequisiteGroup(tuple(prerequisite(p) for p in group.alternatives))
                    for group in row.prerequisite_groups
                ),
            )
            if row.attribute is not None and row.difficulty is not None
            else None
        )
        template = (
            TechniqueTemplate(
                row.template.difficulty,
                row.template.default_modifier,
                row.template.maximum_modifier,
                tuple(f"skill:{parent}" for parent in row.template.parents),
                f"skill:{row.template.parent_family}" if row.template.parent_family else None,
                attributes[row.template.attribute] if row.template.attribute else None,
            )
            if row.template
            else None
        )
        variable = (
            VariableFamily(
                row.variable.subject,
                row.variable.determination,
                f"skill:{row.variable.mirrors}" if row.variable.mirrors else None,
                attributes[row.variable.attribute] if row.variable.attribute else None,
                row.variable.difficulty,
            )
            if row.variable
            else None
        )
        if spec is not None:
            definition = RuleDefinition(
                identifier,
                DefinitionKind.SKILL,
                row.name,
                SOURCE.id,
                None,
                ImplementationStatus.UNSUPPORTED,
                skill=spec,
            )
        blockers: list[str] = list(row.blockers)
        # A template or an open family records its contextual shape; only a row
        # with neither a definition nor a shape is still missing its metadata.
        if definition is None and template is None and variable is None:
            blockers.append("metadata-audit")
        dispatch: str | None = None
        bound = [group[identifier] for group in BINDINGS if identifier in group]
        if len(bound) > 1:
            raise ValidationError(f"Row is bound by more than one procedure group: {identifier}")
        procedure = bound[0] if bound else None
        transferred: tuple[tuple[str, tuple[int, ...]], ...] = ()
        if procedure is not None:
            # A binding may only resolve or keep the blockers this inventory
            # recorded, its numbers must be the recorded ones, and every blocker
            # it keeps must name a child that the row already owns.
            if set(procedure.resolved) | set(procedure.blockers) != set(row.blockers):
                raise ValidationError(f"Runtime binding disagrees with the inventory: {identifier}")
            if procedure.spec() != spec:
                raise ValidationError(f"Runtime binding changes recorded mechanics: {identifier}")
            if any(not owners for owners in procedure.transferred.values()):
                raise ValidationError(f"Transferred blocker names no owner: {identifier}")
            blockers = [b for b in blockers if b not in procedure.resolved]
            transferred = tuple(procedure.transferred.items())
            if procedure.dispatchable:
                definition = procedure.definition()
                dispatch = procedure.dispatch
        result.append(
            SkillAudit(
                identifier,
                row.name,
                definition.skill.reference if definition and definition.skill else f"B{row.page}",
                definition,
                tuple(blockers),
                row.issues,
                row.specialty_required,
                row.tl_required,
                f"skill:{row.alias_of}" if row.alias_of else None,
                row.procedure_owner,
                template,
                variable,
                bound=procedure is not None and procedure.implemented,
                dispatch=dispatch,
                transferred=transferred,
            )
        )
    entries = tuple(result)
    validate_inventory(entries)
    validate_source_index(entries, exclusions())
    return entries


def candidate_package() -> RulesPackage:
    """Separate immutable package; unsupported entries cannot activate campaigns."""
    return RulesPackage(
        "package:gurps-mundane-skill-candidates",
        "0.3.0",
        "gurps-4e-2004",
        (SOURCE,),
        tuple(
            RuleDefinition(
                entry.id,
                DefinitionKind.SKILL,
                entry.name,
                SOURCE.id,
                None,
                ImplementationStatus.UNSUPPORTED,
                skill=entry.definition.skill if entry.definition else None,
            )
            for entry in inventory()
        ),
    )


def cross_package_prerequisites() -> frozenset[str]:
    """Prerequisites this chapter records but another catalog owns.

    B182 Brain Hacking requires Computer Hacking, which the #119 supernatural
    catalog carries. The reference is real source data, so it is resolved against
    that catalog rather than dropped for being outside this inventory.
    """
    from wayfarer.rules.supernatural import inventory as owning_catalog

    owned = {entry.id for entry in owning_catalog().entries if entry.kind == "skill"}
    missing = CROSS_PACKAGE - owned
    if missing:
        raise ValidationError(
            f"Cross-package prerequisite is unowned: {', '.join(sorted(missing))}"
        )
    return CROSS_PACKAGE


def validate_inventory(entries: tuple[SkillAudit, ...]) -> None:
    identifiers = {e.id for e in entries}
    cross_package = cross_package_prerequisites()
    if len(identifiers) != len(entries):
        raise ValidationError("Duplicate skill inventory ID")
    for entry in entries:
        if not entry.reference or not entry.followup_issues:
            raise ValidationError("Inventory entries require references and explicit owners")
        if entry.definition and entry.definition.skill:
            if entry.definition.id != entry.id:
                raise ValidationError(f"Mismatched skill definition ID for {entry.id}")
            implemented = entry.definition.status is ImplementationStatus.IMPLEMENTED
            if implemented != (entry.dispatch is not None):
                raise ValidationError(f"Only a dispatched row may be implemented: {entry.id}")
            if entry.dispatch is not None and (
                not entry.bound or entry.dispatch not in entry.definition.hooks
            ):
                raise ValidationError(f"Dispatched row must carry its hook: {entry.id}")
            if not implemented and entry.definition.status is not ImplementationStatus.UNSUPPORTED:
                raise ValidationError(f"Provisional definition must be unsupported: {entry.id}")
            spec = entry.definition.skill
            allowed_targets = identifiers | {a.value for a in A}
            if any(d.target not in allowed_targets or d.target == entry.id for d in spec.defaults):
                raise ValidationError(f"Invalid default references for {entry.id}")
            references = tuple(d.target for d in spec.defaults if d.target.startswith("skill:"))
            # A prerequisite another catalog owns resolves there, not here.
            references += tuple(
                p.target
                for p in spec.prerequisites
                + tuple(p for g in spec.prerequisite_groups for p in g.alternatives)
                if p.kind is PrerequisiteKind.TRAINED_SKILL and p.target not in cross_package
            )
            if spec.technique:
                references += (spec.technique.parent,)
            if spec.specialty and spec.specialty.optional_parent:
                references += (spec.specialty.optional_parent,)
            if spec.specialty:
                references += (f"skill:{spec.specialty.family}",)
            if not set(references) <= identifiers:
                raise ValidationError(f"Unresolved skill references for {entry.id}")
            alternatives = tuple(
                p for group in spec.prerequisite_groups for p in group.alternatives
            )
            if any(
                p.target == entry.id or p.minimum < 1 for p in spec.prerequisites + alternatives
            ):
                raise ValidationError(f"Invalid prerequisite references for {entry.id}")
            if any(len(group.alternatives) < 2 for group in spec.prerequisite_groups):
                raise ValidationError(f"An alternative set needs alternatives: {entry.id}")
        if entry.template is not None:
            permitted = set(entry.template.parents)
            if entry.id in permitted or not permitted <= identifiers:
                raise ValidationError(f"Unresolved technique template parents for {entry.id}")
            family = entry.template.parent_family
            if family is not None and family not in identifiers:
                raise ValidationError(f"Unresolved technique template family for {entry.id}")
        if entry.variable is not None and entry.variable.mirrors is not None:
            if entry.variable.mirrors not in identifiers or entry.variable.mirrors == entry.id:
                raise ValidationError(f"Invalid variable family mirror for {entry.id}")
        if not entry.structural_classes:
            raise ValidationError(f"Unclassified inventory row: {entry.id}")
        if OWNER not in entry.followup_issues:
            raise ValidationError(f"Inventory row leaves this audit unowned: {entry.id}")
        if entry.procedure_owner in (0, OWNER, CONTEXT_OWNER):
            raise ValidationError(f"Missing procedure owner: {entry.id}")
        if any(
            not set(owners) <= set(entry.followup_issues)
            for owners in entry.blocker_owners.values()
        ):
            raise ValidationError(f"Unowned blocker: {entry.id}")
        if entry.alias_of and (entry.alias_of not in identifiers or entry.alias_of == entry.id):
            raise ValidationError(f"Invalid alias reference: {entry.id}")
    by_id = {entry.id: entry for entry in entries}
    dependencies: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        dependency_spec = entry.definition.skill if entry.definition else None
        # A prerequisite another catalog owns is not a node in this graph.
        parents = (
            tuple(
                p.target
                for p in dependency_spec.prerequisites
                if p.kind is PrerequisiteKind.TRAINED_SKILL and p.target in by_id
            )
            if dependency_spec
            else ()
        )
        if dependency_spec:
            # An alternative is an acquisition edge too: a cycle through one is
            # still a cycle, even though only one alternative need be satisfied.
            parents += tuple(
                p.target
                for group in dependency_spec.prerequisite_groups
                for p in group.alternatives
                if p.kind is PrerequisiteKind.TRAINED_SKILL and p.target in by_id
            )
        if dependency_spec and dependency_spec.technique:
            parents += (dependency_spec.technique.parent,)
        if (
            dependency_spec
            and dependency_spec.specialty
            and dependency_spec.specialty.optional_parent
        ):
            parents += (dependency_spec.specialty.optional_parent,)
        dependencies[entry.id] = parents

    visited: set[str] = set()
    active: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in active:
            raise ValidationError(f"Cyclic prerequisite or technique reference: {identifier}")
        if identifier in visited:
            return
        active.add(identifier)
        for parent in dependencies[identifier]:
            visit(parent)
        active.remove(identifier)
        visited.add(identifier)

    # Mutual defaults (e.g. Broadsword/Shortsword) are legitimate source data.
    # Acquisition prerequisites and parent-relative techniques are not cycles.
    for identifier in dependencies:
        visit(identifier)
    for entry in entries:
        seen = {entry.id}
        target = entry.alias_of
        while target:
            if target in seen:
                raise ValidationError(f"Cyclic alias reference: {entry.id}")
            seen.add(target)
            target = by_id[target].alias_of
    sampled = {structural for entry in entries for structural in entry.structural_classes}
    missing = sorted(set(StructuralClass) - sampled)
    if missing:
        raise ValidationError(f"Structural classes are unsampled: {', '.join(missing)}")


def require_available(identifier: str) -> RuleDefinition:
    entry = next((e for e in inventory() if e.id == identifier), None)
    if entry is None:
        raise ValidationError(f"Skill not in the declared source inventory: {identifier}")
    if not entry.available or entry.definition is None:
        raise ValidationError(f"Skill unavailable: {identifier}: {', '.join(entry.blockers)}")
    return entry.definition


def audit_report() -> dict[str, object]:
    entries = inventory()
    validate_inventory(entries)
    excluded = transferred_exclusions()
    validate_source_index(entries, excluded)
    return {
        "profile": PROFILE,
        "source_id": SOURCE.id,
        "baseline": "Characters third printing (February 2008), exact artifact pinned",
        "inventory_completeness": "B301-B304 reconciled; contextual expansions explicitly blocked",
        "source_index": source_index().model_dump(mode="json"),
        "skills": [
            asdict(entry)
            | {
                "implementation": entry.implementation,
                "structural_classes": [c.value for c in entry.structural_classes],
                "owners": list(entry.owners),
                "blocker_owners": entry.blocker_owners,
            }
            for entry in entries
        ],
        "excluded": [e.model_dump() for e in excluded],
        "excluded_total": len(excluded),
        "blocker_counts": dict(sorted(Counter(b for e in entries for b in e.blockers).items())),
        "structural_class_counts": {
            structural.value: sum(structural in e.structural_classes for e in entries)
            for structural in StructuralClass
        },
        "implementation_counts": dict(sorted(Counter(e.implementation for e in entries).items())),
        "coverage_blockers": list(coverage_blockers(PROFILE)),
        # Rows whose only recorded issue is this audit have no named mechanics
        # owner yet; that is a visible certification blocker for #122, not silence.
        "runtime_owner_unassigned": sum(not entry.owners for entry in entries),
        # A bound row can still leave part of its entry to another issue. That is
        # not a blocker, and publishing it keeps the gap visible to #122.
        "transferred_procedure_scope": [
            {"skill": identifier} | asdict(scope)
            for identifier, scope in (*social_scope(), *ranged_scope())
        ],
        # A bound row can also execute while the capability live play consults
        # is still unverified. That is not a blocker on the row, and publishing
        # it keeps #358's gap visible to the scenario and character validators.
        "unverified_activation_scope": [
            {"skill": identifier} | asdict(scope) for identifier, scope in technology_scope()
        ],
        "structured": sum(e.definition is not None for e in entries),
        "required_specialties": sum(e.specialty_required for e in entries),
        "technology_level_dependent": sum(e.tl_required for e in entries),
        "total": len(entries),
        "available": sum(e.available for e in entries),
    }
