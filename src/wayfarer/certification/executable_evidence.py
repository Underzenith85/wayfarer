"""Join explicit mechanic obligations to source cases and current executed assertions.

A registry entry is a claim, never evidence by itself. Passing JUnit cases must
record the case and checkout fingerprints after checking the behavioral result.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from wayfarer.certification.source_ledgers import BASELINE_ID, SOURCE_DIGESTS, AuditRecord
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY

MANIFEST = Path("src/wayfarer/certification/basic_set_audit/executable-evidence.json")
FIXTURE = Path("tests/fixtures/gurps/conformance.json")
PROFILE = "gurps-basic-set-4e-2004"


class CaseBinding(AuditRecord):
    case_id: str
    tests: tuple[str, ...] = Field(min_length=1)


class ObligationBinding(AuditRecord):
    identifier: str
    case_ids: tuple[str, ...] = Field(min_length=1)


class ExecutionManifest(AuditRecord):
    schema_version: Literal[1]
    baseline_id: str
    source_sha256: dict[str, str]
    cases: tuple[CaseBinding, ...]
    obligations: tuple[ObligationBinding, ...]


def checkout_digest(root: Path) -> str:
    """Fingerprint runtime, tests, runners and toolchain pins, excluding generated files."""
    digest = hashlib.sha256()
    paths = sorted(
        [
            path
            for directory in ("src", "tests", "scripts")
            for path in (root / directory).rglob("*")
            if path.is_file() and path.suffix in {".py", ".json"}
        ]
        + [
            root / name
            for name in ("pyproject.toml", "uv.lock", ".python-version", "server.py")
            if (root / name).is_file()
        ]
    )
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def case_digest(case: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()


def provenance(root: Path, case: dict[str, object]) -> dict[str, str]:
    selected = max(
        (p for p in DEFAULT_REGISTRY.profiles if p.conformance_profile_id == PROFILE),
        key=lambda p: p.version,
    )
    return {
        "conformance_case_id": str(case["id"]),
        "conformance_case_digest": case_digest(case),
        "baseline_id": BASELINE_ID,
        "profile_digest": selected.digest,
        "checkout_digest": checkout_digest(root),
    }


@dataclass(frozen=True)
class ExecutionEvidence:
    verified: frozenset[str]
    problems: dict[str, str]
    error: str | None = None

    def problem(self, identifier: str) -> str | None:
        if self.error is not None:
            return self.error
        if identifier in self.verified:
            return None
        return self.problems.get(identifier, "No explicit executable conformance case binding")


def evaluate_execution(root: Path, report: Path | None) -> ExecutionEvidence:
    """Reject missing, stale, skipped, duplicate or status-only execution evidence."""
    try:
        manifest = ExecutionManifest.model_validate_json((root / MANIFEST).read_text())
        fixture: dict[str, object] = json.loads((root / FIXTURE).read_text())
        raw_cases = fixture["cases"]
        if not isinstance(raw_cases, list):
            raise ValueError("Malformed conformance cases")
        cases: dict[str, dict[str, object]] = {}
        for case in raw_cases:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str):
                raise ValueError("Malformed conformance case")
            if case["id"] in cases:
                raise ValueError("Duplicate conformance case")
            cases[case["id"]] = case
        if (
            manifest.baseline_id != BASELINE_ID
            or fixture.get("baseline_id") != BASELINE_ID
            or manifest.source_sha256 != SOURCE_DIGESTS
        ):
            raise ValueError("Stale executable evidence source pins")
        bindings = {entry.case_id: entry for entry in manifest.cases}
        obligations = {entry.identifier: entry for entry in manifest.obligations}
        if len(bindings) != len(manifest.cases) or len(obligations) != len(manifest.obligations):
            raise ValueError("Duplicate executable evidence binding")
        if report is None:
            raise ValueError("No executed conformance report supplied")
        observed: dict[str, list[ET.Element]] = {}
        for test in ET.parse(report).getroot().iter("testcase"):
            node = test.get("classname", "").replace(".", "/") + ".py::" + test.get("name", "")
            observed.setdefault(node, []).append(test)
        fingerprint = checkout_digest(root)
        selected = max(
            (p for p in DEFAULT_REGISTRY.profiles if p.conformance_profile_id == PROFILE),
            key=lambda p: p.version,
        )
        failures: dict[str, str] = {}
        for identifier, binding in bindings.items():
            case = cases.get(identifier)
            if case is None or (
                case.get("profile") != PROFILE
                or case.get("source_id")
                not in {"sjg:basic-set-characters-4e-2004", "sjg:basic-set-campaigns-4e-2004"}
                or not all(
                    case.get(key) for key in ("reference", "provenance", "input", "expected")
                )
            ):
                failures[identifier] = "Missing or incomplete source-derived case"
                continue
            for node in binding.tests:
                matches = observed.get(node, [])
                if len(matches) != 1:
                    failures[identifier] = "Missing or duplicate executed assertion"
                    break
                test = matches[0]
                if any(test.find(tag) is not None for tag in ("failure", "error", "skipped")):
                    failures[identifier] = "Conformance assertion did not pass"
                    break
                properties = test.findall("properties/property")
                expected = {
                    "conformance_case_id": identifier,
                    "conformance_case_digest": case_digest(case),
                    "baseline_id": BASELINE_ID,
                    "profile_digest": selected.digest,
                    "checkout_digest": fingerprint,
                }
                if any(
                    [p.get("value") for p in properties if p.get("name") == name] != [value]
                    for name, value in expected.items()
                ):
                    failures[identifier] = "Missing or stale behavioral execution provenance"
                    break
        problems: dict[str, str] = {}
        for identifier, obligation in obligations.items():
            for case_id in obligation.case_ids:
                if case_id not in bindings:
                    problems[identifier] = "Unregistered conformance case ID"
                    break
                if case_id in failures:
                    problems[identifier] = f"{case_id}: {failures[case_id]}"
                    break
                if identifier.startswith("capability:") and cases[case_id].get(
                    "capability_id"
                ) != identifier.removeprefix("capability:"):
                    problems[identifier] = "Conformance case belongs to another capability"
                    break
        return ExecutionEvidence(frozenset(obligations.keys() - problems.keys()), problems)
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        return ExecutionEvidence(frozenset(), {}, f"Unreadable execution evidence: {exc}")
