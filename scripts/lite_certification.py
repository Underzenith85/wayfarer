"""Fail-closed Lite certification evidence, separate from prototype release evidence."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wayfarer.certification.equipment_audit import lite_gaps
from wayfarer.engine.rules.conformance import BASELINE_ID, CAPABILITIES, PROFILES, CoverageStatus
from wayfarer.engine.rules.profiles import GURPS_LITE_PROFILE

ROOT = Path(__file__).resolve().parents[1]
TARGET = "gurps-lite-4e-2004"
SOURCE = "sjg:gurps-lite-4e-2004"
SOURCE_VERSION = "August 2004 electronic edition, Rev. 07/12/04; no errata overlay"


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FixtureEvidence(EvidenceModel):
    fixture_id: str = Field(min_length=1)
    tests: list[str]


class CapabilityEvidence(EvidenceModel):
    capability_id: str = Field(min_length=1)
    fixtures: list[FixtureEvidence]


class CertificationManifest(EvidenceModel):
    schema_version: Literal[1]
    profile_id: str
    profile_version: int
    profile_digest: str
    baseline_id: str
    source_id: str
    source_version: str
    fixture_digest: str
    source_audit: Literal["pending", "reviewed"]
    catalog_audit: Literal["pending", "reviewed"]
    pending: list[str]
    capabilities: list[CapabilityEvidence]
    # Exact JUnit node IDs for real-service character creation through adventure play.
    journeys: list[str]


def evaluate_lite(
    report: Path,
    manifest_path: Path = ROOT / "tests/fixtures/gurps/lite-certification.json",
    ledger_path: Path = ROOT / "tests/fixtures/gurps/conformance.json",
) -> dict[str, object]:
    """Require exact pins, reviewed scope, fixture bindings and passing live evidence.

    This consumes the same JUnit output as the prototype release checker. It does
    not infer executed fixtures from module names or accept prototype journeys.
    """
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    selected = GURPS_LITE_PROFILE
    result: dict[str, object] = {
        "scope": TARGET,
        "profile_id": selected.id,
        "profile_version": selected.version,
        "profile_digest": selected.digest,
        "baseline_id": BASELINE_ID,
        "source_id": SOURCE,
        "source_version": SOURCE_VERSION,
        "passed": False,
        "mechanics": rows,
        "errors": errors,
    }
    try:
        manifest = CertificationManifest.model_validate_json(manifest_path.read_text())
        ledger_bytes = ledger_path.read_bytes()
        ledger = json.loads(ledger_bytes)
        cases = list(ET.parse(report).getroot().iter("testcase"))
    except (OSError, ValueError, ET.ParseError) as exc:
        errors.append(f"Unreadable certification evidence: {exc}")
        return result
    if not isinstance(ledger, dict):
        errors.append("Malformed fixture ledger")
        return result
    expected_pins = {
        "profile_id": selected.id,
        "profile_version": selected.version,
        "profile_digest": selected.digest,
        "baseline_id": BASELINE_ID,
        "source_id": SOURCE,
        "source_version": SOURCE_VERSION,
        "fixture_digest": hashlib.sha256(ledger_bytes).hexdigest(),
    }
    for field, expected in expected_pins.items():
        if getattr(manifest, field) != expected:
            errors.append(f"Certification pin mismatch: {field}")
    if ledger.get("baseline_id") != BASELINE_ID:
        errors.append("Fixture baseline mismatch")
    if manifest.source_audit != "reviewed":
        errors.append("Frozen Lite source audit pending")
    if manifest.catalog_audit != "reviewed":
        errors.append("Item-level Lite trait/skill/equipment audit pending")
    # #180 records Lite equipment gaps separately; deferring Basic Set catalog work
    # never lets the Lite claim through.
    errors.extend(
        f"Lite equipment gap: {gap.id} (#{gap.owner_issue}) {gap.detail}" for gap in lite_gaps()
    )
    errors.extend(f"Certification prerequisite pending: {item}" for item in manifest.pending)
    required = PROFILES[TARGET].required_capabilities
    if selected.required_capabilities != required:
        errors.append("Registered profile differs from required Lite subset")
    declared = [entry.capability_id for entry in manifest.capabilities]
    if len(declared) != len(set(declared)) or set(declared) != required:
        errors.append("Certification inventory must contain exactly every required Lite capability")
    observed: dict[str, list[ET.Element]] = {}
    for case in cases:
        node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "")
        observed.setdefault(node, []).append(case)
    if not cases:
        errors.append("Test report contains no cases")

    def require_test(node: str) -> None:
        matches = observed.get(node, [])
        if len(matches) != 1:
            errors.append(f"Missing or duplicate Lite evidence: {node}")
        elif any(matches[0].find(tag) is not None for tag in ("failure", "error", "skipped")):
            errors.append(f"Non-passing Lite evidence: {node}")
        else:
            properties = {
                prop.get("name"): prop.get("value")
                for prop in matches[0].findall("properties/property")
            }
            if properties.get("profile_digest") != selected.digest:
                errors.append(f"Wrong or missing Lite profile provenance: {node}")
            if properties.get("baseline_id") != BASELINE_ID:
                errors.append(f"Wrong or missing Lite source provenance: {node}")

    fixtures = ledger.get("cases", [])
    if not isinstance(fixtures, list) or any(not isinstance(case, dict) for case in fixtures):
        errors.append("Malformed fixture ledger")
        return result
    fixture_ids = [case.get("id") for case in fixtures]
    if any(not isinstance(identifier, str) or not identifier for identifier in fixture_ids):
        errors.append("Malformed fixture identifiers")
        return result
    if len(fixture_ids) != len(set(fixture_ids)):
        errors.append("Duplicate fixture identifiers")
    for identifier in sorted(required):
        capability = CAPABILITIES[identifier]
        if capability.status != CoverageStatus.VERIFIED:
            errors.append(f"Unverified Lite mechanic: {identifier} ({capability.status.value})")
        bindings = [entry for entry in manifest.capabilities if entry.capability_id == identifier]
        relevant = [
            case
            for case in fixtures
            if case.get("profile") == TARGET and case.get("capability_id") == identifier
        ]
        expected_ids = {case["id"] for case in relevant}
        bound = [fixture for entry in bindings for fixture in entry.fixtures]
        bound_ids = [fixture.fixture_id for fixture in bound]
        if (
            not expected_ids
            or set(bound_ids) != expected_ids
            or len(bound_ids) != len(set(bound_ids))
        ):
            errors.append(f"Missing or mismatched Lite fixture bindings: {identifier}")
        for case in relevant:
            if (
                case.get("source_id") != SOURCE
                or not case.get("reference")
                or not case.get("provenance")
                or not case.get("input")
                or not case.get("expected")
            ):
                errors.append(f"Incomplete source-derived fixture: {case['id']}")
        for binding in bound:
            if not binding.tests:
                errors.append(f"Missing executable fixture evidence: {binding.fixture_id}")
            for node in binding.tests:
                require_test(node)
        rows.append(
            {
                "capability": identifier,
                "status": capability.status.value,
                "fixtures": len(relevant),
                "owner_issue": capability.owner_issue,
            }
        )
    if not manifest.journeys:
        errors.append("Missing real-service Lite character-to-adventure journey")
    for node in manifest.journeys:
        require_test(node)
    result["passed"] = not errors
    return result
