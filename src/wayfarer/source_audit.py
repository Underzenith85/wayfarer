"""Evidence accounting, separate from runtime capability and campaign profile pins."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import BASELINE_ID, CAPABILITIES, PROFILES
from wayfarer.rules.mundane_skills import inventory as skills
from wayfarer.rules.mundane_traits import inventory as traits
from wayfarer.rules.profiles import (
    GURPS_CAMPAIGNS_PACKAGE,
    GURPS_CHARACTERS_PACKAGE,
    GURPS_LITE_PACKAGE,
    GURPS_MAGIC_PACKAGE,
)
from wayfarer.rules.supernatural import inventory as supernatural_inventory
from wayfarer.simulation.basic_equipment import BASIC_EQUIPMENT, ULTRATECH_INDEX, VEHICLE_INDEX


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_min_length=1)


class Source(Record):
    id: str
    target: str
    observed: str
    reference: str
    status: Literal["unavailable", "different-printing", "reviewed"]
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    blockers: tuple[int, ...]
    note: str


class Scope(Record):
    id: str
    source_id: str
    reference: str
    owner: int = Field(gt=0)
    decision: Literal["required", "optional-disabled", "excluded", "unresolved"]
    reviewed: bool = False
    reason: str


class FixtureReview(Record):
    id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "compared", "reviewed"]
    reviewer: str | None = None
    evidence: str | None = None
    tests: tuple[str, ...]
    owner: int = Field(gt=0)


class Manifest(Record):
    schema_version: Literal[1]
    baseline_id: str
    sources: tuple[Source, ...]
    scopes: tuple[Scope, ...]
    fixtures: tuple[FixtureReview, ...]


@dataclass(frozen=True)
class InventoryItem:
    id: str
    reference: str
    owner: int
    implementation: str
    scope: str
    required_profiles: tuple[str, ...] = ("gurps-basic-set-4e-2004",)
    source_review: str = "pending"
    blockers: tuple[int, ...] = ()


def inventory() -> tuple[InventoryItem, ...]:
    """Read owner inventories; candidate counts never imply exhaustive source coverage."""
    rows = [InventoryItem(e.id, e.reference, 112, "partial", "mundane-skills") for e in skills()]
    rows.extend(
        InventoryItem(e.id, e.reference, 113, "partial", "mundane-traits") for e in traits()
    )
    # Consume the owner inventory directly, including transferred skill exclusions.
    rows.extend(
        InventoryItem(
            "supernatural/" + e.id,
            f"B{e.page}",
            119,
            e.status.value,
            "supernatural-skills" if e.kind == "skill" else "supernatural-catalog",
            () if e.optional else ("gurps-basic-set-4e-2004",),
            blockers=e.blockers,
        )
        for e in supernatural_inventory().entries
    )
    for package in (
        GURPS_LITE_PACKAGE,
        GURPS_CHARACTERS_PACKAGE,
        GURPS_CAMPAIGNS_PACKAGE,
        GURPS_MAGIC_PACKAGE,
    ):
        for d in package.definitions:
            owner = 119 if d.id.startswith("spell:") or "supernatural" in d.hooks else 180
            if d.id.startswith(("attribute:", "secondary:")):
                owner = 97
            elif d.id.startswith("skill:"):
                owner = 112
            elif d.id.startswith("trait:") and owner != 119:
                owner = 113
            rows.append(
                InventoryItem(
                    package.id + "@" + package.version + "/" + d.id,
                    d.source_id,
                    owner,
                    d.status.value,
                    "registered-catalog",
                    ("gurps-lite-4e-2004",)
                    if package.id == GURPS_LITE_PACKAGE.id
                    else ("gurps-basic-set-4e-2004",),
                )
            )
    rows.extend(
        InventoryItem(
            e.definition_id,
            ", ".join(f"B{p}" for p in e.provenance.pages),
            180,
            "partial" if not e.unsupported_mechanics else "unsupported",
            "equipment-catalog",
        )
        for e in (*BASIC_EQUIPMENT.entries, *ULTRATECH_INDEX)
    )
    rows.extend(
        InventoryItem(e.definition_id, f"B{e.page}", 207, "listing-only", "vehicle-catalog")
        for e in VEHICLE_INDEX
    )
    return tuple(rows)


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def load(root: Path) -> Manifest:
    return Manifest.model_validate_json(
        (root / "tests/fixtures/gurps/source-audit.json").read_text()
    )


def validate(root: Path, manifest: Manifest) -> None:
    """Reject stale reviews, missing ownership, invalid references and coverage drift."""
    if manifest.baseline_id != BASELINE_ID:
        raise ValidationError("Audit baseline differs from frozen profile")
    for records in (manifest.sources, manifest.scopes, manifest.fixtures, inventory()):
        ids = [r.id for r in records]
        if len(ids) != len(set(ids)):
            raise ValidationError("Duplicate audit identifier")
    required_scopes = {
        "lite-rules-and-catalog",
        "basic-uncatalogued-rules",
        "basic-campaign-rules",
        "mundane-skills",
        "mundane-traits",
        "supernatural-skills",
        "supernatural-catalog",
        "equipment-catalog",
        "vehicle-catalog",
        "statistics-boundaries",
    }
    if not required_scopes <= {s.id for s in manifest.scopes}:
        raise ValidationError("Missing required audit scope")
    source_ids = {s.id for s in manifest.sources}
    required_sources = {s for p in PROFILES.values() for s in p.source_ids}
    required_sources.update(
        s + ":errata-2007-01-26" for s in tuple(required_sources) if "basic-set" in s
    )
    if not required_sources <= source_ids:
        raise ValidationError("Missing profile source")
    for source in manifest.sources:
        if not source.note or not source.reference:
            raise ValidationError("Source needs provenance and reference")
        if source.status != "reviewed" and not source.blockers:
            raise ValidationError("Unreviewed source needs an owner issue")
        if source.status == "reviewed" and (source.blockers or not source.sha256):
            raise ValidationError("Reviewed source needs a digest and resolved blockers")
    for scope in manifest.scopes:
        if scope.source_id not in source_ids or not scope.reference or not scope.reason:
            raise ValidationError("Invalid scope reference or decision")
    ledger = json.loads((root / "tests/fixtures/gurps/conformance.json").read_text())
    cases = {case["id"]: case for case in ledger["cases"]}
    reviews = {review.id: review for review in manifest.fixtures}
    if set(cases) != set(reviews):
        raise ValidationError("Every fixture needs an explicit audit disposition")
    for identifier, review in reviews.items():
        case = cases[identifier]
        if fingerprint(case) != review.sha256:
            raise ValidationError(f"Stale fixture review: {identifier}")
        if case["source_id"] not in source_ids or case["capability_id"] not in CAPABILITIES:
            raise ValidationError("Unknown fixture source or capability")
        profile = PROFILES.get(case["profile"])
        if profile is None or case["source_id"] not in profile.source_ids:
            raise ValidationError("Fixture source outside selected profile")
        if case["capability_id"] not in profile.required_capabilities:
            raise ValidationError("Fixture capability outside selected profile")
        if review.status != "pending" and (not review.reviewer or not review.evidence):
            raise ValidationError("Reviewed fixture needs independent review evidence")
        if review.status == "reviewed":
            source = next(s for s in manifest.sources if s.id == case["source_id"])
            if source.status != "reviewed":
                raise ValidationError("Frozen fixture review requires reconciled source")
        if not review.tests:
            raise ValidationError("Fixture needs an executable evidence binding")
        for binding in review.tests:
            filename, separator, function = binding.partition("::")
            path = root / filename
            if not separator or not path.is_file() or f"def {function}(" not in path.read_text():
                raise ValidationError(f"Missing fixture test: {binding}")
    documented = {}
    for line in (root / "docs/gurps-conformance.md").read_text().splitlines():
        if line.startswith("| `gurps."):
            parts = [p.strip().strip("`") for p in line.split("|")[1:-1]]
            identifier, lite, basic, status, owner = parts
            if identifier in documented:
                raise ValidationError("Duplicate documented capability")
            documented[identifier] = (lite, basic, status)
            if (
                identifier not in CAPABILITIES
                or f"#{CAPABILITIES[identifier].owner_issue}" not in owner
            ):
                raise ValidationError(f"Documentation owner drift: {identifier}")
    expected = {
        c.id: (
            "yes" if c.lite_required else "no",
            "yes" if c.basic_required else "no",
            c.status.value,
        )
        for c in CAPABILITIES.values()
    }
    if documented != expected:
        raise ValidationError("Documentation capability coverage drift")


def blockers(manifest: Manifest) -> tuple[str, ...]:
    """Source completeness cannot be inferred from implemented family/item counts."""
    result = [f"source:{s.id}" for s in manifest.sources if s.status != "reviewed"]
    result.extend(f"scope:{s.id}" for s in manifest.scopes if not s.reviewed)
    result.extend(f"fixture:{f.id}" for f in manifest.fixtures if f.status != "reviewed")
    return tuple(result)


def report(root: Path) -> dict[str, object]:
    manifest = load(root)
    validate(root, manifest)
    return {
        "baseline_id": manifest.baseline_id,
        "audit_complete": not blockers(manifest),
        "blockers": blockers(manifest),
        "sources": [s.model_dump() for s in manifest.sources],
        "scopes": [s.model_dump() for s in manifest.scopes],
        "inventory": [asdict(i) for i in inventory()],
        "fixtures": [f.model_dump() for f in manifest.fixtures],
    }
