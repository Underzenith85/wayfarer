"""Source-indexed Basic Set supernatural coverage, never an execution allowlist.

The audit is separate from saved rules packages. Whole-entry support remains
blocked even where a narrower pinned binding can execute a representative case.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.conformance import BASELINE_ID, CoverageStatus, profile
from wayfarer.errors import ValidationError
from wayfarer.models import Record

PROFILE = "gurps-basic-set-4e-2004"


class AuditModel(Record):
    """Base for supernatural inventory audit rows."""


class ObservedSource(AuditModel):
    id: str
    title: str
    edition: Literal[4]
    printing: int = Field(ge=1)
    publication: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    errata_overlay: str | None
    baseline_reconciled: bool
    blocker: int = Field(gt=0)


class Entry(AuditModel):
    id: str
    name: str
    kind: Literal["spell", "advantage", "disadvantage", "power", "protocol", "skill"]
    page: int = Field(ge=1, le=576)
    classification: Literal["spell", "X", "Sup", "psi-member", "psi", "magic", "cinematic-skill"]
    difficulty: Literal["H", "VH"] | None
    colleges: tuple[str, ...]
    blockers: tuple[int, ...]
    source: str
    status: CoverageStatus
    supported_subset: str
    evidence: tuple[str, ...]
    members: tuple[str, ...]
    member_conditions: tuple[str, ...]
    talent_cost: int | None
    power_modifier: int | None
    optional: bool

    @model_validator(mode="after")
    def coverage_integrity(self) -> Self:
        if not self.id.startswith(self.kind + ":") or not self.name.strip():
            raise ValueError("Entry identifier must match its kind and name")
        if any(n <= 0 for n in self.blockers) or len(set(self.blockers)) != len(self.blockers):
            raise ValueError("Invalid or duplicate blocker")
        if self.status is CoverageStatus.VERIFIED:
            if self.blockers or not self.evidence or not self.supported_subset:
                raise ValueError("Verified entries require evidence and no blockers")
        elif not self.blockers:
            raise ValueError("Unverified entries require concrete blockers")
        if self.status is CoverageStatus.PARTIAL and (
            not self.evidence or not self.supported_subset
        ):
            raise ValueError("Partial entries require explicit subset evidence")
        if self.kind == "spell" and (self.difficulty is None or not self.colleges):
            raise ValueError("Spell difficulty and colleges required")
        if self.kind != "spell" and (self.difficulty is not None or self.colleges):
            raise ValueError("Only spells declare spell learning metadata")
        if self.kind == "power":
            if not self.members or self.power_modifier is None:
                raise ValueError("Power membership and modifier required")
        elif (
            self.members
            or self.member_conditions
            or self.talent_cost is not None
            or self.power_modifier is not None
        ):
            raise ValueError("Only powers declare power metadata")
        return self


class Inventory(AuditModel):
    version: Literal[1]
    profile: Literal["gurps-basic-set-4e-2004"]
    baseline: str
    sources: tuple[ObservedSource, ...] = Field(min_length=1)
    entries: tuple[Entry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self) -> Self:
        if self.baseline != BASELINE_ID:
            raise ValueError("Unexpected conformance baseline")
        sources = {s.id: s for s in self.sources}
        entries = {e.id: e for e in self.entries}
        if len(sources) != len(self.sources) or len(entries) != len(self.entries):
            raise ValueError("Duplicate source or entry identifier")
        for entry in self.entries:
            source = sources.get(entry.source)
            if source is None:
                raise ValueError("Unknown observed source")
            if not source.baseline_reconciled and (
                source.blocker not in entry.blockers or entry.status is CoverageStatus.VERIFIED
            ):
                raise ValueError("Unreconciled source must remain a coverage blocker")
            for member in entry.members:
                if member not in entries or entries[member].kind != "advantage":
                    raise ValueError("Power member must reference an inventoried advantage")
            if len(set(entry.members)) != len(entry.members):
                raise ValueError("Duplicate power member")
        return self


def inventory() -> Inventory:
    """Read typed package data without relying on the process working directory."""
    return Inventory.model_validate_json(Path(__file__).with_name("inventory.json").read_text())


def lookup(identifier: str) -> Entry:
    for entry in inventory().entries:
        if entry.id == identifier:
            return entry
    raise ValidationError(f"Unknown supernatural entry: {identifier}")


def require_entries(profile_id: str, identifiers: tuple[str, ...]) -> tuple[Entry, ...]:
    """Whole-entry gate for validators; no fuzzy names or model-created fallback.

    Profile validation still happens for empty requirements. Individual runtime
    bindings continue to use the exact approved package and channel validators.
    This inventory neither publishes those bindings nor migrates a campaign.
    """
    profile(profile_id)
    if profile_id != PROFILE:
        raise ValidationError("Supernatural inventory is outside the selected profile")
    result = tuple(lookup(identifier) for identifier in identifiers)
    for entry in result:
        if entry.optional or entry.status is not CoverageStatus.VERIFIED or entry.blockers:
            blockers = ", ".join(f"#{n}" for n in entry.blockers)
            raise ValidationError(f"Supernatural entry is not certified: {entry.id}; {blockers}")
    return result


def definition(identifier: str) -> RuleDefinition:
    """Non-purchasable authoring record, compatible with existing catalog gates.

    Even partial runtime evidence does not make an entire source entry a legal
    purchase. Future execution must publish an explicitly versioned binding.
    Audit IDs are distinct from the existing representative package definitions.
    """
    entry = lookup(identifier)
    if entry.kind not in ("spell", "advantage", "disadvantage", "skill"):
        raise ValidationError("Powers and protocols are not purchasable definitions")
    return RuleDefinition(
        "audit:" + entry.id,
        DefinitionKind.SKILL if entry.kind in ("spell", "skill") else DefinitionKind.TRAIT,
        entry.name,
        "audit:" + entry.source,
        None,
        ImplementationStatus.UNSUPPORTED,
        hooks=("supernatural",),
    )


def coverage_blockers(profile_id: str) -> tuple[int, ...]:
    """Aggregate explicit item blockers for certification and authoring reports."""
    profile(profile_id)
    if profile_id != PROFILE:
        raise ValidationError("Supernatural inventory is outside the selected profile")
    return tuple(sorted({issue for entry in inventory().entries for issue in entry.blockers}))


def require_family(capability_id: str) -> None:
    """A family flag cannot override unresolved item-level coverage."""
    if capability_id == "gurps.magic.spellcasting":
        kinds = {"spell", "protocol"}
    elif capability_id == "gurps.supernatural.abilities":
        kinds = {"advantage", "disadvantage", "power", "skill"}
    else:
        raise ValidationError(f"Unknown supernatural family: {capability_id}")
    require_entries(
        PROFILE,
        tuple(e.id for e in inventory().entries if e.kind in kinds and not e.optional),
    )
