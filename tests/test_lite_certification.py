"""Adversarial certification-gate tests; synthetic evidence is not rules evidence."""

import json
from dataclasses import replace
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

import pytest

from scripts import lite_certification as gate
from scripts import release_gates
from wayfarer.rules.conformance import BASELINE_ID, CAPABILITIES, PROFILES, CoverageStatus
from wayfarer.rules.profiles import GURPS_LITE_PROFILE
from wayfarer.simulation.equipment_audit import lite_gaps


def test_current_lite_certification_is_explicitly_blocked(tmp_path: Path) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.test_wave14" name="test_play"/></testsuite>'
    )
    result = gate.evaluate_lite(report)
    assert result["passed"] is False
    assert result["profile_digest"] == GURPS_LITE_PROFILE.digest
    assert result["source_version"] == gate.SOURCE_VERSION
    errors = str(result["errors"])
    assert "Unverified Lite mechanic" in errors
    assert "Missing real-service Lite" in errors
    assert "source audit pending" in errors
    assert "Lite equipment gap: lite-weapon-table (#121)" in errors


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "absent",
        "manual",
        "partial",
        "missing_capability",
        "extra_capability",
        "missing_fixture",
        "missing_binding",
        "missing_test",
        "skipped",
        "failure",
        "error",
        "duplicate_test",
        "prototype",
        "source",
        "pin",
        "ledger",
        "audit",
        "catalog",
        "equipment",
        "journey",
    ],
)
def test_gate_requires_complete_pinned_passing_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
) -> None:
    # Small synthetic inventory exercises the acceptance logic. It never updates
    # the checked-in inventory or claims any actual mechanic is certified.
    identifier = "gurps.check.success"
    selected = replace(GURPS_LITE_PROFILE, required_capabilities=frozenset({identifier}))
    monkeypatch.setattr(gate, "GURPS_LITE_PROFILE", selected)
    monkeypatch.setattr(
        gate,
        "PROFILES",
        {
            gate.TARGET: replace(
                PROFILES[gate.TARGET], required_capabilities=frozenset({identifier})
            )
        },
    )
    status = (
        CoverageStatus(defect)
        if defect in ("absent", "manual", "partial")
        else CoverageStatus.VERIFIED
    )
    monkeypatch.setattr(
        gate, "CAPABILITIES", {identifier: replace(CAPABILITIES[identifier], status=status)}
    )
    gaps = lite_gaps() if defect == "equipment" else ()
    monkeypatch.setattr(gate, "lite_gaps", lambda: gaps)
    manifest = gate.CertificationManifest.model_validate_json(
        (gate.ROOT / "tests/fixtures/gurps/lite-certification.json").read_text()
    )
    manifest.profile_digest = selected.digest
    manifest.source_audit = "reviewed"
    manifest.catalog_audit = "reviewed"
    manifest.pending = []
    manifest.capabilities = [
        entry for entry in manifest.capabilities if entry.capability_id == identifier
    ]
    node = "tests/test_lite_live.py::test_character_to_adventure"
    for binding in manifest.capabilities[0].fixtures:
        binding.tests = [node]
    manifest.journeys = [node]
    if defect == "missing_capability":
        manifest.capabilities = []
    if defect == "extra_capability":
        manifest.capabilities.append(gate.CapabilityEvidence(capability_id="unknown", fixtures=[]))
    if defect == "missing_fixture":
        manifest.capabilities[0].fixtures = []
    if defect == "missing_binding":
        manifest.capabilities[0].fixtures[0].tests = []
    if defect == "pin":
        manifest.profile_digest = "prototype"
    if defect == "ledger":
        manifest.fixture_digest = "stale"
    if defect == "audit":
        manifest.source_audit = "pending"
    if defect == "catalog":
        manifest.catalog_audit = "pending"
    if defect == "journey":
        manifest.journeys = []
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json())
    suite = Element("testsuite")
    if defect != "missing_test":
        case = SubElement(
            suite, "testcase", classname="tests.test_lite_live", name="test_character_to_adventure"
        )
        props = SubElement(case, "properties")
        SubElement(
            props,
            "property",
            name="profile_digest",
            value="prototype" if defect == "prototype" else selected.digest,
        )
        SubElement(
            props,
            "property",
            name="baseline_id",
            value="wrong" if defect == "source" else BASELINE_ID,
        )
        if defect in ("skipped", "failure", "error"):
            SubElement(case, defect)
        if defect == "duplicate_test":
            suite.append(case)
    report = tmp_path / "junit.xml"
    ElementTree(suite).write(report)
    result = gate.evaluate_lite(report, path)
    assert result["passed"] is (defect == "none"), result["errors"]


def test_cli_writes_blocked_report_for_missing_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["release_gates", str(tmp_path / "missing.xml"), "--gurps-lite", "--output", str(tmp_path)],
    )
    with pytest.raises(SystemExit, match="Lite certification blocked"):
        release_gates.main()
    result = json.loads((tmp_path / "lite-certification.json").read_text())
    assert result["passed"] is False
    assert result["scope"] == gate.TARGET
    assert result["profile_digest"] == GURPS_LITE_PROFILE.digest
