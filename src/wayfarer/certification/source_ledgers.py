"""Durable, exhaustive Basic Set source-ledger accounting.

These records describe the selected source, never runtime behavior.  Runtime
catalogs may be linked from a row, but the engine does not import this module.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from pydantic import ConfigDict, Field

from wayfarer.errors import ValidationError
from wayfarer.models import Record

BASIC_PROFILE_ID: Final = "gurps-basic-set-4e-2004"
BASELINE_ID: Final = "gurps-4e-characters-3p-2008+campaigns-4p-2008"
LEDGER_DIRECTORY: Final = Path("src/wayfarer/certification/basic_set_audit")
EXPECTED_LEDGER_COUNTS: Final = {"sections": 711, "traits": 487, "modifiers": 87}
SOURCE_DIGESTS: Final = {
    "characters-third": "872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e",
    "campaigns-fourth": "79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80",
}


class AuditRecord(Record):
    model_config = ConfigDict(str_min_length=1)


class SourceLedgerRow(AuditRecord):
    id: str = Field(pattern=r"^(section|trait|modifier):[a-z0-9][a-z0-9:-]*$")
    row_kind: Literal[
        "structural-section",
        "mechanic",
        "catalog-item",
        "optional-rule",
        "example",
        "reference",
        "setting",
        "rollup",
    ]
    source_id: Literal["characters-third", "campaigns-fourth"]
    title: str
    printed_page: int = Field(ge=1, le=576)
    list_page: int | None = Field(default=None, ge=1, le=576)
    profile_membership: tuple[str, ...]
    disposition: Literal[
        "required",
        "optional-unresolved",
        "optional-disabled",
        "excluded",
        "reference-only",
        "setting-unresolved",
    ]
    implementation: Literal[
        "absent", "unsupported", "partial", "implemented", "verified", "not-applicable"
    ]
    source_review: Literal["pending", "reviewed"]
    evidence_paths: tuple[str, ...]
    historical_owners: tuple[int, ...]
    completion_owner: int | None = Field(default=None, gt=0)
    capability_id: str | None = None
    runtime_binding: str | None = None
    parent_id: str | None = None
    classification: str | None = None
    listed_value: str | None = None
    construction_binding: str | None = None
    cost_owner: str | None = None
    consequence_owner: int | None = Field(default=None, gt=0)
    source_review_owner: int | None = Field(default=None, gt=0)


class SourceLedger(AuditRecord):
    schema_version: Literal[1]
    ledger_type: Literal["sections", "traits", "modifiers"]
    baseline_id: str
    source_sha256: dict[str, str]
    rows: tuple[SourceLedgerRow, ...]


class CompletionIssue(AuditRecord):
    issue: int = Field(gt=0)
    state: Literal["open", "closed"]
    title: str


class CompletionOwners(AuditRecord):
    schema_version: Literal[1]
    repository: str
    verified_at: str
    issues: tuple[CompletionIssue, ...]


class Denominator(AuditRecord):
    schema_version: Literal[1]
    baseline_id: str
    ledger_counts: dict[str, int]
    legacy_inventory_count: int = Field(ge=0)
    identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class LedgerBundle:
    rows: tuple[SourceLedgerRow, ...]
    by_type: dict[str, tuple[SourceLedgerRow, ...]]
    owners: CompletionOwners
    denominator: Denominator


class InventoryLedgerRow(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def scope(self) -> str: ...

    @property
    def required_profiles(self) -> tuple[str, ...]: ...

    @property
    def implementation(self) -> str: ...

    @property
    def source_review(self) -> str: ...

    @property
    def owner(self) -> int: ...


@dataclass(frozen=True, slots=True)
class TraitReconciliation:
    """One exhaustive trait-list disposition, separate from execution state."""

    source_row_id: str
    trait_kind: Literal["advantage", "disadvantage", "perk", "quirk", "rollup"]
    classifications: frozenset[Literal["mental", "physical", "social"]]
    source_class: Literal["mundane", "exotic", "supernatural", "combined", "rollup"]
    construction: Literal["runtime-definition", "source-value-recorded", "not-applicable"]
    cost_owner: str | None
    consequence_owner: int | None
    runtime_binding: str | None
    available: bool


def _trait_classification(
    row: SourceLedgerRow,
) -> tuple[
    Literal["advantage", "disadvantage", "perk", "quirk", "rollup"],
    frozenset[Literal["mental", "physical", "social"]],
    Literal["mundane", "exotic", "supernatural", "combined", "rollup"],
]:
    if row.row_kind == "rollup":
        return "rollup", frozenset(), "rollup"
    if row.classification is None:
        raise ValidationError(f"Trait row lacks classification: {row.id}")
    fields = row.classification.split("|")
    if len(fields) != 3:
        raise ValidationError(f"Malformed trait classification: {row.id}")
    kind_value, attribute_value, source_value = fields
    kind = cast(
        Literal["advantage", "disadvantage", "perk", "quirk"],
        (
            "perk"
            if kind_value == "advantage" and row.printed_page >= 100 and row.listed_value == "1"
            else "quirk"
            if kind_value == "disadvantage" and row.printed_page >= 162 and row.listed_value == "-1"
            else kind_value
        ),
    )
    if kind not in {"advantage", "disadvantage", "perk", "quirk"}:
        raise ValidationError(f"Unknown trait kind: {row.id}")
    attributes: set[Literal["mental", "physical", "social"]] = set()
    if "M" in attribute_value:
        attributes.add("mental")
    if "P" in attribute_value:
        attributes.add("physical")
    if "Soc" in attribute_value:
        attributes.add("social")
    if not attributes:
        raise ValidationError(f"Trait row lacks M/P/Soc classification: {row.id}")
    source_marker = source_value.split()[0]
    source_class: Literal["mundane", "exotic", "supernatural", "combined"]
    if source_marker in {"–", "-"}:
        source_class = "mundane"
    elif source_marker == "X":
        source_class = "exotic"
    elif source_marker == "Sup":
        source_class = "supernatural"
    elif source_marker == "X/Sup":
        source_class = "combined"
    else:
        raise ValidationError(f"Unknown trait source classification: {row.id}")
    return kind, frozenset(attributes), source_class


def reconcile_trait_ledger(
    rows: tuple[SourceLedgerRow, ...], inventory_rows: tuple[InventoryLedgerRow, ...]
) -> tuple[TraitReconciliation, ...]:
    """Reconcile every source list row exactly once without claiming execution.

    An unmatched named row keeps its source-listed construction value, but is
    unavailable and remains owned by an open mechanics issue.  A runtime match
    owns cost and consequence validation through its inventory record.  This
    distinction prevents catalog presence from certifying behavior.
    """
    inventory = _inventory_index(inventory_rows)
    reconciled: list[TraitReconciliation] = []
    for row in rows:
        kind, classifications, source_class = _trait_classification(row)
        runtime = None if row.runtime_binding is None else inventory.get(row.runtime_binding)
        if row.runtime_binding is not None and runtime is None:
            raise ValidationError(f"Unknown trait runtime binding: {row.id}")
        is_rollup = row.row_kind == "rollup"
        catalog_only = row.runtime_binding == row.id
        available = runtime is not None and runtime.implementation in {"implemented", "verified"}
        reconciled.append(
            TraitReconciliation(
                source_row_id=row.id,
                trait_kind=kind,
                classifications=classifications,
                source_class=source_class,
                construction=(
                    "not-applicable"
                    if is_rollup
                    else "source-value-recorded"
                    if catalog_only
                    else "runtime-definition"
                    if runtime is not None
                    else "source-value-recorded"
                ),
                cost_owner=None if is_rollup else row.cost_owner or row.runtime_binding or row.id,
                consequence_owner=(
                    None
                    if is_rollup
                    else row.consequence_owner
                    or (runtime.owner if runtime is not None else row.completion_owner)
                ),
                runtime_binding=row.runtime_binding,
                available=available,
            )
        )
    if len(reconciled) != len(rows) or len({row.source_row_id for row in reconciled}) != len(rows):
        raise ValidationError("Every trait source row must reconcile exactly once")
    return tuple(reconciled)


def _inventory_index(
    inventory_rows: tuple[InventoryLedgerRow, ...],
) -> dict[str, InventoryLedgerRow]:
    """Build a lossless join index for source-to-runtime reconciliation."""
    inventory: dict[str, InventoryLedgerRow] = {}
    for row in inventory_rows:
        if row.id in inventory:
            raise ValidationError(f"Duplicate runtime inventory identifier: {row.id}")
        inventory[row.id] = row
    return inventory


def _read_json(root: Path, filename: str) -> str:
    return (root / LEDGER_DIRECTORY / filename).read_text()


def load_source_ledgers(root: Path) -> LedgerBundle:
    by_type: dict[str, tuple[SourceLedgerRow, ...]] = {}
    rows: list[SourceLedgerRow] = []
    for ledger_type in EXPECTED_LEDGER_COUNTS:
        ledger = SourceLedger.model_validate_json(_read_json(root, f"{ledger_type}.json"))
        if (
            ledger.ledger_type != ledger_type
            or ledger.baseline_id != BASELINE_ID
            or ledger.source_sha256 != SOURCE_DIGESTS
        ):
            raise ValidationError(f"Invalid {ledger_type} ledger identity")
        by_type[ledger_type] = ledger.rows
        rows.extend(ledger.rows)
    return LedgerBundle(
        rows=tuple(rows),
        by_type=by_type,
        owners=CompletionOwners.model_validate_json(_read_json(root, "completion-owners.json")),
        denominator=Denominator.model_validate_json(_read_json(root, "denominator.json")),
    )


def denominator_identity(
    rows: tuple[SourceLedgerRow, ...], inventory_rows: tuple[InventoryLedgerRow, ...]
) -> str:
    """Fingerprint membership/identity, not progress fields that change during review."""
    import hashlib

    ledger_identity = [
        {
            "id": row.id,
            "source_id": row.source_id,
            "printed_page": row.printed_page,
            "profile_membership": row.profile_membership,
            "disposition": row.disposition,
        }
        for row in rows
    ]
    inventory_identity = sorted(
        (
            row.id,
            row.scope,
            row.required_profiles,
        )
        for row in inventory_rows
        if BASIC_PROFILE_ID in row.required_profiles
    )
    value = {"source_ledgers": ledger_identity, "runtime_inventory": inventory_identity}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_source_ledgers(
    bundle: LedgerBundle,
    inventory_rows: tuple[InventoryLedgerRow, ...],
    capability_ids: frozenset[str],
) -> None:
    """Fail on malformed imports, dead ownership, duplicate bindings, or drift."""
    if bundle.denominator.baseline_id != BASELINE_ID:
        raise ValidationError("Source-ledger denominator baseline drift")
    if bundle.owners.repository != "Underzenith85/wayfarer":
        raise ValidationError("Source-ledger owner registry repository drift")

    actual_counts = {name: len(rows) for name, rows in bundle.by_type.items()}
    if actual_counts != EXPECTED_LEDGER_COUNTS:
        raise ValidationError(f"Source-ledger row-count drift: {actual_counts}")
    if bundle.denominator.ledger_counts != actual_counts:
        raise ValidationError("Source-ledger denominator count drift")

    ids = [row.id for row in bundle.rows]
    if len(ids) != len(set(ids)):
        raise ValidationError("Duplicate source-ledger identifier")
    row_ids = set(ids)
    open_owners = {issue.issue for issue in bundle.owners.issues if issue.state == "open"}
    if len(bundle.owners.issues) != len({issue.issue for issue in bundle.owners.issues}):
        raise ValidationError("Duplicate completion-owner issue")

    inventory = _inventory_index(inventory_rows)
    bindings: list[str] = []
    for row in bundle.rows:
        low, high = (1, 336) if row.source_id == "characters-third" else (337, 576)
        if not low <= row.printed_page <= high:
            raise ValidationError(f"Source page outside selected printing: {row.id}")
        if row.list_page is not None and not low <= row.list_page <= high:
            raise ValidationError(f"Source list page outside selected printing: {row.id}")
        if BASIC_PROFILE_ID not in row.profile_membership:
            raise ValidationError(f"Basic Set row missing profile membership: {row.id}")
        needs_owner = row.disposition in {
            "required",
            "optional-unresolved",
            "setting-unresolved",
        }
        if needs_owner and row.completion_owner is None:
            raise ValidationError(f"Required row lacks completion owner: {row.id}")
        if row.completion_owner is not None and row.completion_owner not in open_owners:
            raise ValidationError(f"Completion owner is missing or closed: {row.id}")
        if row.capability_id is not None and row.capability_id not in capability_ids:
            raise ValidationError(f"Unknown source-ledger capability: {row.id}")
        if row.parent_id is not None and row.parent_id not in row_ids:
            raise ValidationError(f"Unknown source-ledger parent: {row.id}")
        if row.row_kind == "catalog-item" and row.id.startswith(("trait:", "modifier:")):
            if not row.construction_binding or not row.cost_owner or row.consequence_owner is None:
                raise ValidationError(
                    f"Catalog row lacks separate construction ownership: {row.id}"
                )
            if row.source_review_owner not in open_owners:
                raise ValidationError(f"Catalog row lacks an open source-review owner: {row.id}")
            if row.runtime_binding == row.id and row.consequence_owner not in open_owners:
                raise ValidationError(f"Catalog-only row lacks an open consequence owner: {row.id}")
        if row.runtime_binding is not None:
            runtime = inventory.get(row.runtime_binding)
            if runtime is None:
                raise ValidationError(f"Unknown runtime inventory binding: {row.id}")
            missing_profiles = set(row.profile_membership) - set(runtime.required_profiles)
            if missing_profiles:
                raise ValidationError(f"Runtime inventory binding outside profile: {row.id}")
            if row.source_review == "reviewed" and runtime.source_review != "reviewed":
                raise ValidationError(f"Runtime inventory review is not joined: {row.id}")
            if row.implementation in {"implemented", "verified"} and runtime.implementation not in {
                "implemented",
                "verified",
            }:
                raise ValidationError(f"Runtime inventory implementation disagrees: {row.id}")
            bindings.append(row.runtime_binding)
        if row.implementation in {"implemented", "verified"}:
            if row.source_review != "reviewed" or not row.evidence_paths:
                raise ValidationError(f"Implemented row lacks reviewed evidence: {row.id}")
        if (
            row.source_review == "reviewed"
            and row.disposition
            in {
                "optional-disabled",
                "excluded",
                "reference-only",
            }
            and not row.evidence_paths
        ):
            raise ValidationError(f"Reviewed disposition lacks evidence: {row.id}")
    if len(bindings) != len(set(bindings)):
        duplicates = sorted(key for key, count in Counter(bindings).items() if count > 1)
        raise ValidationError(f"Duplicate runtime inventory ownership: {duplicates[0]}")

    trait_rows = bundle.by_type["traits"]
    reconciled_traits = reconcile_trait_ledger(trait_rows, inventory_rows)
    if len(reconciled_traits) != EXPECTED_LEDGER_COUNTS["traits"]:
        raise ValidationError("Trait reconciliation denominator drift")
    # Same-name positive/negative and repeated list rows retain distinct source
    # identities.  They may share prose labels, never a runtime definition.
    by_title: dict[str, list[TraitReconciliation]] = {}
    for source_row, reconciliation in zip(trait_rows, reconciled_traits, strict=True):
        by_title.setdefault(source_row.title.casefold(), []).append(reconciliation)
    for records in by_title.values():
        runtime_bindings = [record.runtime_binding for record in records if record.runtime_binding]
        if len(runtime_bindings) != len(set(runtime_bindings)):
            raise ValidationError("Trait aliases collapse mechanically distinct source rows")

    required_inventory = tuple(
        row for row in inventory_rows if BASIC_PROFILE_ID in row.required_profiles
    )
    if bundle.denominator.legacy_inventory_count != len(required_inventory):
        raise ValidationError("Runtime catalog denominator drift")
    if bundle.denominator.identity_sha256 != denominator_identity(bundle.rows, inventory_rows):
        raise ValidationError("Basic Set denominator identity drift")


def ledger_blockers(rows: tuple[SourceLedgerRow, ...]) -> tuple[SourceLedgerRow, ...]:
    """Return every row whose source disposition or implementation is incomplete."""
    ready: list[SourceLedgerRow] = []
    for row in rows:
        reviewed_nonmechanic = row.disposition in {
            "optional-disabled",
            "excluded",
            "reference-only",
        } and bool(row.evidence_paths)
        reviewed_mechanic = (
            row.disposition == "required"
            and row.implementation
            in {
                "implemented",
                "verified",
            }
            and bool(row.evidence_paths)
        )
        if row.source_review != "reviewed" or not (reviewed_nonmechanic or reviewed_mechanic):
            ready.append(row)
    return tuple(ready)


def ledger_rollups(rows: tuple[SourceLedgerRow, ...]) -> dict[str, dict[str, int]]:
    return {
        "disposition": dict(sorted(Counter(row.disposition for row in rows).items())),
        "implementation": dict(sorted(Counter(row.implementation for row in rows).items())),
        "source_review": dict(sorted(Counter(row.source_review for row in rows).items())),
        "capability": dict(
            sorted(Counter(row.capability_id or "unassigned" for row in rows).items())
        ),
        "completion_owner": dict(
            sorted(Counter(str(row.completion_owner or "none") for row in rows).items())
        ),
    }
