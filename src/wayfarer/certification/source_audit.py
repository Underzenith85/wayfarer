"""Evidence accounting, separate from runtime capability and campaign profile pins."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final, Literal

from pydantic import ConfigDict, Field

from wayfarer.certification.creature_audit import inventory as creatures
from wayfarer.certification.equipment_audit import (
    ledger as equipment_audit_ledger,
)
from wayfarer.certification.equipment_audit import (
    rows as equipment_audit_rows,
)
from wayfarer.certification.source_ledgers import (
    ledger_blockers,
    ledger_rollups,
    load_source_ledgers,
    validate_source_ledgers,
)
from wayfarer.engine.rules.conformance import BASELINE_ID, CAPABILITIES, PROFILES
from wayfarer.engine.rules.profiles import (
    GURPS_CAMPAIGNS_PACKAGE,
    GURPS_CHARACTERS_PACKAGE,
    GURPS_LITE_PACKAGE,
    GURPS_MAGIC_PACKAGE,
)
from wayfarer.engine.rules.skills.mundane import inventory as skills
from wayfarer.engine.rules.skills.mundane import source_index as skill_source_index
from wayfarer.engine.rules.supernatural import inventory as supernatural_inventory
from wayfarer.engine.rules.traits.modifiers import MODIFIER_INDEX
from wayfarer.engine.rules.traits.mundane import inventory as traits
from wayfarer.engine.rules.types.vehicle_coverage import validate_coverage
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.basic.ultratech import ULTRATECH_INDEX
from wayfarer.engine.simulation.equipment.basic.vehicles import VEHICLE_INDEX
from wayfarer.errors import ValidationError
from wayfarer.models import Record as Entity


class Record(Entity):
    """Audit rows additionally reject empty strings."""

    model_config = ConfigDict(str_min_length=1)


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
    gaps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    obligation: Literal[
        "construction-catalog",
        "executable-mechanic",
        "optional-rule",
        "reference-only",
        "unsupported-required",
    ] = "executable-mechanic"


INVENTORY_SOURCE_REVIEW_EVIDENCE: Final = "docs/gurps-inventory-source-review.md"

# Exact expansion-to-parent joins.  These bindings are intentionally enumerated:
# a future vocabulary value or construction variant must receive its own review.
MUNDANE_TRAIT_SOURCE_BINDINGS: Final = {
    "trait:low-status": "trait:disadvantage:status",
    **dict.fromkeys(
        (
            "trait:shyness-mild",
            "trait:shyness-severe",
            "trait:shyness-crippling",
        ),
        "trait:disadvantage:shyness",
    ),
    "trait:code-of-honor-soldier": "trait:disadvantage:code-of-honor",
    **dict.fromkeys(
        (
            "trait:appearance-hideous",
            "trait:appearance-ugly",
            "trait:appearance-unattractive",
            "trait:appearance-horrific",
            "trait:appearance-monstrous",
        ),
        "trait:disadvantage:appearance",
    ),
    **dict.fromkeys(
        (
            "trait:appearance-average",
            "trait:appearance-attractive",
            "trait:appearance-handsome",
            "trait:appearance-very-handsome",
            "trait:appearance-transcendent",
            "trait:appearance-handsome-androgynous",
            "trait:appearance-handsome-impressive",
            "trait:appearance-very-handsome-androgynous",
            "trait:appearance-very-handsome-impressive",
            "trait:appearance-transcendent-androgynous",
            "trait:appearance-transcendent-impressive",
            "trait:appearance-attractive-universal",
            "trait:appearance-handsome-universal",
            "trait:appearance-very-handsome-universal",
            "trait:appearance-transcendent-universal",
            "trait:appearance-handsome-off-the-shelf",
            "trait:appearance-very-handsome-off-the-shelf",
            "trait:appearance-transcendent-off-the-shelf",
        ),
        "trait:advantage:appearance",
    ),
    **dict.fromkeys(
        (
            "trait:reputation-bravery",
            "trait:reputation-bravery-guild-sometimes",
        ),
        "trait:advantage:reputation",
    ),
    **dict.fromkeys(
        (
            "trait:reputation-cruelty",
            "trait:reputation-cruelty-guild-occasionally",
        ),
        "trait:disadvantage:reputation",
    ),
    "trait:acute-hearing": "trait:advantage:acute-hearing",
    "trait:acute-taste-smell": "trait:advantage:acute-taste-and-smell",
    "trait:acute-touch": "trait:advantage:acute-touch",
    "trait:acute-vision": "trait:advantage:acute-vision",
    **dict.fromkeys(
        (
            "trait:wealth-dead-broke",
            "trait:wealth-poor",
            "trait:wealth-struggling",
        ),
        "trait:disadvantage:wealth",
    ),
    **dict.fromkeys(
        (
            "trait:wealth-average",
            "trait:wealth-comfortable",
            "trait:wealth-wealthy",
            "trait:wealth-very-wealthy",
            "trait:wealth-filthy-rich",
            "trait:wealth-multimillionaire-1",
            "trait:wealth-multimillionaire-2",
            "trait:wealth-multimillionaire-3",
        ),
        "trait:advantage:wealth",
    ),
    **dict.fromkeys(
        (
            "trait:sense-of-duty-individual",
            "trait:sense-of-duty-small-group",
            "trait:sense-of-duty-large-group",
            "trait:sense-of-duty-race",
            "trait:sense-of-duty-all-living",
        ),
        "trait:disadvantage:sense-of-duty",
    ),
    "trait:culture-foreign": "trait:advantage:cultural-familiarity",
    "trait:rank-watch": "trait:advantage:rank",
    "trait:rank-replaces-status-watch": "trait:advantage:rank",
    "trait:courtesy-rank-watch": "trait:advantage:courtesy-rank",
    "trait:ally-associate": "trait:advantage:allies",
    "trait:contact-associate": "trait:advantage:contacts",
    "trait:patron-associate": "trait:advantage:patrons",
    "trait:dependent-associate": "trait:disadvantage:dependents",
    "trait:enemy-associate": "trait:disadvantage:enemies",
}

DIRECT_INVENTORY_SOURCE_REVIEWS: Final = frozenset(
    {
        "trait:language-trade-spoken",
        "trait:language-trade-written",
        "vehicle:wagon",
        "vehicle:luxury-car",
        *(
            f"package:gurps-basic-set-characters-4e-2004@{version}/{definition}"
            for version in ("0.3.0", "0.4.0")
            for definition in (
                "attribute:st",
                "attribute:dx",
                "attribute:iq",
                "attribute:ht",
                "secondary:hp",
                "secondary:will",
                "secondary:per",
                "secondary:fp",
                "secondary:basic-speed",
                "secondary:basic-move",
                "skill:guns-gyroc",
                "skill:guns-smg",
            )
        ),
        "package:gurps-basic-set-characters-4e-2004@0.4.0/trait:magery-0",
        "package:gurps-basic-set-characters-4e-2004@0.4.0/trait:magery",
    }
)

SPECIAL_PENETRATION_MODIFIER_IDS: Final = frozenset(
    {
        "modifier:enhancement:armor-divisor",
        "modifier:enhancement:blood-agent",
        "modifier:enhancement:contact-agent",
        "modifier:enhancement:follow-up",
        "modifier:enhancement:respiratory-agent",
        "modifier:enhancement:sense-based",
        "modifier:enhancement:side-effect",
        "modifier:limitation:armor-divisor",
        "modifier:limitation:blood-agent",
        "modifier:limitation:contact-agent",
        "modifier:limitation:sense-based",
    }
)


def inventory(root: Path | None = None) -> tuple[InventoryItem, ...]:
    """Read owner inventories; candidate counts never imply exhaustive source coverage."""
    skill_index = skill_source_index()
    reviewed_skills = (
        {"skill:" + target for entry in skill_index.entries for target in entry.targets}
        if skill_index.baseline_reconciled
        else set()
    )
    # Item-level skill blockers and certification state come from the owner
    # inventory; a blanket family status would hide unowned runtime coverage.
    rows = [
        InventoryItem(
            e.id,
            e.reference,
            112,
            e.implementation,
            "mundane-skills",
            source_review="reviewed" if e.id in reviewed_skills else "pending",
            blockers=e.followup_issues,
            evidence=e.evidence,
            obligation="reference-only"
            if e.implementation == "contextual"
            else "executable-mechanic",
        )
        for e in skills()
    ]
    supernatural = supernatural_inventory()
    reconciled_sources = {
        source.id for source in supernatural.sources if source.baseline_reconciled
    }
    equipment_ledger = equipment_audit_ledger()
    reviewed_equipment = {
        "equipment:" + selected
        for section in equipment_ledger.sections
        if section.status in ("audited", "reconciled")
        for selected in section.selected
    }
    rows.extend(
        InventoryItem(
            identifier,
            reference,
            499,
            "verified",
            "character-development",
        )
        for identifier, reference in (
            ("development:adventure", "B290-B292"),
            ("development:gained-in-play", "B291"),
            ("development:quick-learning", "B292"),
            ("development:study", "B292-B294"),
            ("development:teachers", "B293"),
            ("development:learnable-advantages", "B294"),
        )
    )
    rows.extend(
        InventoryItem(identifier, reference, 519, "verified", "disease-aging")
        for identifier, reference in (
            ("disease:illness", "B442-B444"),
            ("disease:profiles", "B442-B443"),
            ("disease:contagion", "B443"),
            ("disease:infection", "B444"),
            ("disease:aging", "B20-B21/B444"),
        )
    )
    rows.extend(
        InventoryItem(identifier, reference, 500, "verified", "character-transformations")
        for identifier, reference in (
            ("transformation:campaign", "B294-B296"),
            ("transformation:body-modification", "B294-B295"),
            ("transformation:mind-transfer", "B296"),
            ("transformation:supernatural-affliction", "B296"),
            ("transformation:death-boundary", "B296"),
        )
    )
    # A bound runtime effect is reported as implemented; naming one is still partial.
    rows.extend(
        InventoryItem(
            e.id,
            e.reference,
            113,
            "implemented" if e.implemented else "partial",
            "mundane-traits",
            blockers=e.followup_issues,
        )
        for e in traits()
    )
    rows.extend(
        InventoryItem(
            identifier,
            f"B{MODIFIER_INDEX[identifier].page}",
            513,
            "verified",
            "ability-modifier-ledger",
            source_review="reviewed",
            evidence=("tests/test_special_damage.py",),
        )
        for identifier in sorted(SPECIAL_PENETRATION_MODIFIER_IDS)
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
            "reviewed" if e.source in reconciled_sources else "pending",
            blockers=e.blockers,
        )
        for e in supernatural.entries
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
                    evidence=("tests/test_profiles.py",),
                    obligation="construction-catalog",
                )
            )
    rows.extend(
        InventoryItem(
            e.definition_id,
            ", ".join(f"B{p}" for p in e.provenance.pages),
            180,
            "partial" if not e.unsupported_mechanics else "unsupported",
            "equipment-catalog",
            source_review="reviewed" if e.definition_id in reviewed_equipment else "pending",
            evidence=("tests/test_basic_equipment.py",),
            obligation="unsupported-required"
            if e.unsupported_mechanics
            else "construction-catalog",
        )
        for e in (*BASIC_EQUIPMENT.entries, *ULTRATECH_INDEX)
    )
    # #180 item-level rows: omitted table groups, footnote dispositions, field
    # provenance, package binding and Lite gaps are visible blockers, not silence.
    rows.extend(
        InventoryItem(
            e.id,
            e.reference,
            e.owner,
            e.implementation,
            e.scope,
            e.required_profiles,
            e.source_review,
            blockers=e.blockers,
            evidence=e.evidence,
            obligation=(
                "reference-only"
                if e.id
                in {
                    "equipment-field/MeleeMode.kind",
                    "equipment-field/RangedMode.kind",
                }
                else "construction-catalog"
                if e.scope
                in {
                    "equipment-sections",
                    "equipment-field-provenance",
                    "equipment-package-binding",
                }
                else "unsupported-required"
                if e.implementation in {"unsupported", "omitted"}
                else "executable-mechanic"
            ),
        )
        for e in equipment_audit_rows()
    )
    rows.extend(
        InventoryItem(
            e.definition_id,
            f"B{e.page}",
            207,
            "listing-only",
            "vehicle-catalog",
            evidence=("tests/test_basic_equipment.py",),
            obligation="construction-catalog",
        )
        for e in VEHICLE_INDEX
    )
    rows.extend(
        InventoryItem(
            entry.id,
            entry.reference,
            entry.owner,
            entry.implementation,
            entry.scope,
            entry.required_profiles,
            entry.source_review,
            entry.blockers,
            entry.gaps,
        )
        for entry in creatures()
    )
    # Exhaustive source-list records that do not yet have an executable
    # RuleDefinition still exist as unavailable catalog rows.  Their stable IDs
    # let source reconciliation and downstream blockers refer to the same item
    # without making catalog presence imply runtime support.
    audit_directory = Path(__file__).with_name("basic_set_audit")
    executable_ids = {row.id for row in rows}
    for filename, scope in (
        ("traits.json", "mundane-trait-ledger"),
        ("modifiers.json", "ability-modifier-ledger"),
    ):
        source_rows = json.loads((audit_directory / filename).read_text())["rows"]
        for source_row in source_rows:
            if source_row["row_kind"] != "catalog-item":
                continue
            binding = source_row["runtime_binding"]
            if binding != source_row["id"] or binding in executable_ids:
                continue
            owner = int(source_row["consequence_owner"])
            rows.append(
                InventoryItem(
                    binding,
                    f"B{source_row['printed_page']}",
                    owner,
                    "partial" if filename == "modifiers.json" else "unsupported",
                    scope,
                    blockers=(owner,),
                )
            )
    ledger_root = root or Path(__file__).resolve().parents[3]
    bundle = load_source_ledgers(ledger_root)
    reviewed_bindings = {
        row.runtime_binding: row
        for row in bundle.rows
        if row.runtime_binding is not None and row.source_review == "reviewed"
    }
    joined_rows = []
    for row in rows:
        binding_id = MUNDANE_TRAIT_SOURCE_BINDINGS.get(row.id, row.id)
        source_binding = reviewed_bindings.get(binding_id)
        exact_review = row.id in DIRECT_INVENTORY_SOURCE_REVIEWS
        parent_review = source_binding is not None and set(
            source_binding.profile_membership
        ) <= set(row.required_profiles)
        if exact_review or parent_review:
            evidence = row.evidence
            if exact_review or row.id in MUNDANE_TRAIT_SOURCE_BINDINGS:
                evidence = tuple(dict.fromkeys((*evidence, INVENTORY_SOURCE_REVIEW_EVIDENCE)))
            row = replace(row, source_review="reviewed", evidence=evidence)
        joined_rows.append(row)
    joined = tuple(joined_rows)
    reviewed_inventory = {row.id: row for row in joined if row.source_review == "reviewed"}
    result = []
    for row in joined:
        if row.scope != "registered-catalog" or row.source_review == "reviewed":
            result.append(row)
            continue
        definition_id = row.id.partition("/")[2]
        candidates = (definition_id, "supernatural/" + definition_id)
        source = next(
            (
                reviewed_inventory[candidate]
                for candidate in candidates
                if candidate in reviewed_inventory
                and set(row.required_profiles)
                <= set(reviewed_inventory[candidate].required_profiles)
            ),
            None,
        )
        result.append(replace(row, source_review="reviewed") if source else row)
    return tuple(result)


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
        "equipment-sections",
        "equipment-footnotes",
        "equipment-field-provenance",
        "equipment-package-binding",
        "lite-equipment-gaps",
        "vehicle-catalog",
        "statistics-boundaries",
    }
    if not required_scopes <= {s.id for s in manifest.scopes}:
        raise ValidationError("Missing required audit scope")
    source_ids = {s.id for s in manifest.sources}
    required_sources = {s for p in PROFILES.values() for s in p.source_ids}
    if not required_sources <= source_ids:
        raise ValidationError("Missing profile source")
    for source in manifest.sources:
        if not source.note or not source.reference:
            raise ValidationError("Source needs provenance and reference")
        if source.status != "reviewed" and not source.blockers:
            raise ValidationError("Unreviewed source needs an owner issue")
        if source.status == "reviewed" and (source.blockers or not source.sha256):
            raise ValidationError("Reviewed source needs a digest and resolved blockers")
    inventory_rows = inventory()
    evidence_scopes = {"mundane-skills"} | {
        row.scope for row in inventory_rows if row.scope.startswith("equipment-")
    }
    for row in inventory_rows:
        if row.scope in evidence_scopes and not row.evidence:
            raise ValidationError(f"Inventory row needs item-level evidence: {row.id}")
        if row.implementation in {"manual-adjudication", "contextual", "listing-only"} and (
            row.obligation == "executable-mechanic"
        ):
            raise ValidationError(f"Non-executable status needs an explicit obligation: {row.id}")
        if row.obligation in {"construction-catalog", "reference-only"} and not row.evidence:
            raise ValidationError(f"Non-executable obligation needs evidence: {row.id}")
        if row.obligation == "unsupported-required" and row.implementation not in {
            "unsupported",
            "omitted",
        }:
            raise ValidationError(f"Unsupported obligation has a ready status: {row.id}")
        for filename in row.evidence:
            if not (root / filename).is_file():
                raise ValidationError(f"Missing inventory evidence: {row.id}: {filename}")
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
    # The two vehicle rows are derived from a per-mode audit rather than set by
    # hand, so the release pipeline checks that derivation here (#358).
    validate_coverage()
    source_ledgers = load_source_ledgers(root)
    validate_source_ledgers(source_ledgers, inventory(root), frozenset(CAPABILITIES), root)


def blockers(manifest: Manifest) -> tuple[str, ...]:
    """Source completeness cannot be inferred from implemented family/item counts."""
    result = [f"source:{s.id}" for s in manifest.sources if s.status != "reviewed"]
    result.extend(f"scope:{s.id}" for s in manifest.scopes if not s.reviewed)
    result.extend(f"fixture:{f.id}" for f in manifest.fixtures if f.status != "reviewed")
    return tuple(result)


def profile_blockers(root: Path, manifest: Manifest, profile_id: str) -> tuple[str, ...]:
    """Return only source evidence that belongs to one conformance profile."""
    selected = PROFILES.get(profile_id)
    if selected is None:
        raise ValidationError(f"Unknown source-audit profile: {profile_id}")
    source_ids = selected.source_ids
    result = [
        f"source:{source.id}"
        for source in manifest.sources
        if source.id in source_ids and source.status != "reviewed"
    ]
    result.extend(
        f"scope:{scope.id}"
        for scope in manifest.scopes
        if scope.source_id in source_ids and not scope.reviewed
    )
    cases = {
        case["id"]: case
        for case in json.loads((root / "tests/fixtures/gurps/conformance.json").read_text())[
            "cases"
        ]
    }
    result.extend(
        f"fixture:{fixture.id}"
        for fixture in manifest.fixtures
        if cases[fixture.id]["profile"] == profile_id and fixture.status != "reviewed"
    )
    return tuple(result)


def report(root: Path, *, profile_id: str | None = None) -> dict[str, object]:
    manifest = load(root)
    validate(root, manifest)
    source_ledgers = load_source_ledgers(root)
    ledger_rows = source_ledgers.rows
    manifest_blockers = (
        blockers(manifest) if profile_id is None else profile_blockers(root, manifest, profile_id)
    )
    relevant_ledger_rows = (
        ledger_rows
        if profile_id is None
        else tuple(row for row in ledger_rows if profile_id in row.profile_membership)
    )
    all_blockers = (
        *manifest_blockers,
        *(f"ledger:{row.id}" for row in ledger_blockers(relevant_ledger_rows)),
    )
    return {
        "baseline_id": manifest.baseline_id,
        "audit_complete": not all_blockers,
        "blockers": all_blockers,
        "sources": [s.model_dump() for s in manifest.sources],
        "scopes": [s.model_dump() for s in manifest.scopes],
        "inventory": [asdict(i) for i in inventory()],
        "fixtures": [f.model_dump() for f in manifest.fixtures],
        "source_ledgers": {
            name: [row.model_dump() for row in rows]
            for name, rows in source_ledgers.by_type.items()
        },
        "source_ledger_rollups": ledger_rollups(ledger_rows),
    }
