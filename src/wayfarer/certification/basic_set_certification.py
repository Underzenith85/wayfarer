"""Fail-closed release accounting for the frozen GURPS Basic Set profile.

Certification is deliberately separate from runtime mechanics. It reconciles the
existing conformance registry, exact source audit and item-level inventories and
never upgrades coverage because a generic hook or LLM fallback exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

from wayfarer.certification.source_audit import InventoryItem, inventory
from wayfarer.certification.source_audit import report as source_audit_report
from wayfarer.certification.source_ledgers import (
    SourceLedgerRow,
    ledger_rollups,
    load_source_ledgers,
)
from wayfarer.engine.rules.conformance import CAPABILITIES, PROFILES, CoverageStatus
from wayfarer.engine.rules.profiles import (
    BASIC_SET_CONTENT_BOUNDARIES,
    BASIC_SET_OPTIONAL_RULES,
    DEFAULT_REGISTRY,
    RegisteredProfile,
)
from wayfarer.errors import ValidationError

PROFILE_ID: Final = "gurps-basic-set-4e-2004"


@dataclass(frozen=True, slots=True)
class CertificationBlocker:
    kind: Literal["source", "ledger", "capability", "inventory", "profile"]
    identifier: str
    detail: str
    owner_issue: int | None = None


@dataclass(frozen=True, slots=True)
class CertificationReport:
    profile_id: str
    profile_version: int
    profile_digest: str
    source_baseline: str
    source_audit_complete: bool
    required_capabilities: int
    verified_capabilities: int
    required_inventory_items: int
    source_ledger_rows: int
    required_source_ledger_rows: int
    source_ledger_rollups: dict[str, dict[str, int]]
    excluded_content: tuple[str, ...]
    blockers: tuple[CertificationBlocker, ...]

    @property
    def certified(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "profile_digest": self.profile_digest,
            "source_baseline": self.source_baseline,
            "source_audit_complete": self.source_audit_complete,
            "required_capabilities": self.required_capabilities,
            "verified_capabilities": self.verified_capabilities,
            "required_inventory_items": self.required_inventory_items,
            "source_ledger_rows": self.source_ledger_rows,
            "required_source_ledger_rows": self.required_source_ledger_rows,
            "source_ledger_rollups": self.source_ledger_rollups,
            "excluded_content": list(self.excluded_content),
            "certified": self.certified,
            "blockers": [asdict(blocker) for blocker in self.blockers],
        }


def _latest_registered_profile() -> RegisteredProfile:
    candidates = tuple(
        profile
        for profile in DEFAULT_REGISTRY.profiles
        if profile.conformance_profile_id == PROFILE_ID
    )
    if not candidates:
        raise ValidationError(f"No registered profile for {PROFILE_ID}")
    return max(candidates, key=lambda candidate: candidate.version)


READY_IMPLEMENTATIONS: Final = frozenset({"implemented", "verified"})


def _inventory_ready(item: InventoryItem, source_row: SourceLedgerRow | None) -> bool:
    """Require runtime and bound source evidence to agree before promotion."""
    if item.source_review != "reviewed" or item.implementation not in READY_IMPLEMENTATIONS:
        return False
    if source_row is None:
        return True
    return (
        source_row.disposition == "required"
        and source_row.source_review == "reviewed"
        and source_row.implementation in READY_IMPLEMENTATIONS
    )


def _inventory_blocker_detail(item: InventoryItem, source_row: SourceLedgerRow | None) -> str:
    detail = f"implementation={item.implementation}; source_review={item.source_review}"
    if item.gaps:
        detail += "; gaps=" + ",".join(item.gaps)
    if source_row is None:
        return detail
    return (
        f"{detail}; source_ledger_disposition={source_row.disposition}; "
        f"source_ledger_implementation={source_row.implementation}; "
        f"source_ledger_review={source_row.source_review}"
    )


def evaluate(root: Path) -> CertificationReport:
    """Return complete Basic Set certification accounting without mutating state."""
    target = PROFILES[PROFILE_ID]
    selected = _latest_registered_profile()
    audit = source_audit_report(root, profile_id=PROFILE_ID)
    audit_blockers = cast(tuple[str, ...], audit["blockers"])
    source_ledgers = load_source_ledgers(root)
    ledger_by_id = {row.id: row for row in source_ledgers.rows}
    ledger_by_runtime_binding = {
        row.runtime_binding: row for row in source_ledgers.rows if row.runtime_binding is not None
    }
    source_baseline = cast(str, audit["baseline_id"])
    source_complete = cast(bool, audit["audit_complete"])
    blockers: list[CertificationBlocker] = []

    for identifier in audit_blockers:
        if identifier.startswith("ledger:"):
            row = ledger_by_id[identifier.removeprefix("ledger:")]
            blockers.append(
                CertificationBlocker(
                    kind="ledger",
                    identifier=row.id,
                    detail=(
                        f"disposition={row.disposition}; implementation={row.implementation}; "
                        f"source_review={row.source_review}"
                    ),
                    owner_issue=row.completion_owner,
                )
            )
            continue
        blockers.append(
            CertificationBlocker(
                kind="source",
                identifier=identifier,
                detail="Frozen source, scope or fixture review is incomplete or stale",
                owner_issue=191,
            )
        )

    required_capabilities = tuple(sorted(target.required_capabilities))
    for identifier in required_capabilities:
        capability = CAPABILITIES[identifier]
        if capability.status is not CoverageStatus.VERIFIED:
            blockers.append(
                CertificationBlocker(
                    kind="capability",
                    identifier=identifier,
                    detail=f"Required capability is {capability.status.value}",
                    owner_issue=capability.owner_issue,
                )
            )

    required_inventory = tuple(
        item for item in inventory(root) if PROFILE_ID in item.required_profiles
    )
    for item in required_inventory:
        source_row = ledger_by_runtime_binding.get(item.id)
        if not _inventory_ready(item, source_row):
            blockers.append(
                CertificationBlocker(
                    kind="inventory",
                    identifier=item.id,
                    detail=_inventory_blocker_detail(item, source_row),
                    owner_issue=(
                        source_row.completion_owner
                        if source_row is not None
                        and source_row.implementation not in READY_IMPLEMENTATIONS
                        and source_row.completion_owner is not None
                        else item.blockers[0]
                        if item.blockers
                        else item.owner
                    ),
                )
            )

    named = {selection.id: selection.enabled for selection in selected.named_optional_rules}
    if selected.optional_rules or set(named) != set(BASIC_SET_OPTIONAL_RULES):
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Named optional rules require an exact reviewed profile disposition",
            )
        )
    elif any(named.values()):
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Enabled named optional rules are not executable in this profile",
            )
        )
    content = {selection.id: selection.included for selection in selected.content_boundaries}
    if set(content) != set(BASIC_SET_CONTENT_BOUNDARIES):
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Selected-source content requires an exact reviewed profile boundary",
            )
        )
    elif any(content.values()):
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Included selected-source content is not executable in this profile",
            )
        )
    if selected.required_capabilities != target.required_capabilities:
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Registered profile capability set differs from frozen Basic Set target",
            )
        )
    if selected.supported and blockers:
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Profile advertises support before full Basic Set certification evidence passes",
            )
        )

    return CertificationReport(
        profile_id=selected.id,
        profile_version=selected.version,
        profile_digest=selected.digest,
        source_baseline=source_baseline,
        source_audit_complete=source_complete,
        required_capabilities=len(required_capabilities),
        verified_capabilities=sum(
            CAPABILITIES[identifier].status is CoverageStatus.VERIFIED
            for identifier in required_capabilities
        ),
        required_inventory_items=len(required_inventory),
        source_ledger_rows=len(source_ledgers.rows),
        required_source_ledger_rows=sum(
            row.disposition == "required" for row in source_ledgers.rows
        ),
        source_ledger_rollups=ledger_rollups(source_ledgers.rows),
        excluded_content=tuple(
            identifier for identifier, included in content.items() if not included
        ),
        blockers=tuple(blockers),
    )


def require_certified(root: Path) -> CertificationReport:
    """Reject a Basic Set release unless every certification dimension is green."""
    result = evaluate(root)
    if result.blockers:
        preview = ", ".join(blocker.identifier for blocker in result.blockers[:5])
        suffix = "" if len(result.blockers) <= 5 else f" (+{len(result.blockers) - 5} more)"
        raise ValidationError(f"GURPS Basic Set certification blocked: {preview}{suffix}")
    return result
