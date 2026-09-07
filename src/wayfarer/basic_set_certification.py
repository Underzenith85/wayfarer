"""Fail-closed release accounting for the frozen GURPS Basic Set profile.

Certification is deliberately separate from runtime mechanics. It reconciles the
existing conformance registry, exact source audit and item-level inventories and
never upgrades coverage because a generic hook or LLM fallback exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Literal, cast

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import CAPABILITIES, PROFILES, CoverageStatus
from wayfarer.rules.profiles import DEFAULT_REGISTRY, RegisteredProfile
from wayfarer.source_audit import InventoryItem, inventory, report as source_audit_report

PROFILE_ID: Final = "gurps-basic-set-4e-2004"


@dataclass(frozen=True, slots=True)
class CertificationBlocker:
    kind: Literal["source", "capability", "inventory", "profile"]
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


def _inventory_ready(item: InventoryItem) -> bool:
    return item.source_review == "reviewed" and item.implementation in {"implemented", "verified"}


def evaluate(root: Path) -> CertificationReport:
    """Return complete Basic Set certification accounting without mutating state."""
    target = PROFILES[PROFILE_ID]
    selected = _latest_registered_profile()
    audit = source_audit_report(root)
    audit_blockers = cast(list[str], audit["blockers"])
    source_baseline = cast(str, audit["baseline_id"])
    source_complete = cast(bool, audit["audit_complete"])
    blockers: list[CertificationBlocker] = []

    for identifier in audit_blockers:
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
        item for item in inventory() if PROFILE_ID in item.required_profiles
    )
    for item in required_inventory:
        if not _inventory_ready(item):
            blockers.append(
                CertificationBlocker(
                    kind="inventory",
                    identifier=item.id,
                    detail=(
                        f"implementation={item.implementation}; "
                        f"source_review={item.source_review}"
                    ),
                    owner_issue=item.blockers[0] if item.blockers else item.owner,
                )
            )

    if selected.optional_rules:
        blockers.append(
            CertificationBlocker(
                kind="profile",
                identifier=f"{selected.id}@{selected.version}",
                detail="Optional rules must be an explicit reviewed profile selection",
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
