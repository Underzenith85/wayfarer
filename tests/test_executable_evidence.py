"""Certification needs source-derived assertions executed against this checkout."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.certification.executable_evidence import (
    FIXTURE,
    MANIFEST,
    evaluate_execution,
    provenance,
)

ROOT = Path(__file__).resolve().parents[1]


def evidence_root(tmp_path: Path) -> tuple[Path, dict[str, object], str]:
    # A deliberately small immutable source registry exercises the evidence join.
    manifest = json.loads((ROOT / MANIFEST).read_text())
    binding = manifest["cases"][0]
    case = next(
        c
        for c in json.loads((ROOT / FIXTURE).read_text())["cases"]
        if c["id"] == binding["case_id"]
    )
    manifest["cases"] = [binding]
    manifest["obligations"] = [{"identifier": "trait:example", "case_ids": [case["id"]]}]
    for path, data in (
        (MANIFEST, manifest),
        (FIXTURE, {"baseline_id": manifest["baseline_id"], "cases": [case]}),
    ):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(json.dumps(data))
    return tmp_path, case, binding["tests"][0]


def report(root: Path, case: dict[str, object], node: str, *, status: str | None = None) -> Path:
    filename, name = node.split("::", 1)
    suite = ET.Element("testsuite")
    test = ET.SubElement(
        suite, "testcase", classname=filename.removesuffix(".py").replace("/", "."), name=name
    )
    properties = ET.SubElement(test, "properties")
    for key, value in provenance(root, case).items():
        ET.SubElement(properties, "property", name=key, value=value)
    if status:
        ET.SubElement(test, status)
    path = root / "report.xml"
    ET.ElementTree(suite).write(path)
    return path


def test_current_report_can_resolve_a_source_case_to_an_executed_assertion(tmp_path: Path) -> None:
    root, case, node = evidence_root(tmp_path)
    result = evaluate_execution(root, report(root, case, node))
    assert result.problem("trait:example") is None
    assert result.problem("trait:unbound") is not None


@pytest.mark.parametrize("status", ["failure", "error", "skipped"])
def test_nonpassing_assertions_never_certify(tmp_path: Path, status: str) -> None:
    root, case, node = evidence_root(tmp_path)
    result = evaluate_execution(root, report(root, case, node, status=status))
    assert "did not pass" in str(result.problem("trait:example"))


def test_success_receipt_without_behavioral_provenance_does_not_certify(tmp_path: Path) -> None:
    root, case, node = evidence_root(tmp_path)
    path = report(root, case, node)
    tree = ET.parse(path)
    test = next(tree.getroot().iter("testcase"))
    properties = test.find("properties")
    assert properties is not None
    test.remove(properties)
    tree.write(path)
    assert "provenance" in str(evaluate_execution(root, path).problem("trait:example"))


@pytest.mark.parametrize(
    "mutation", ["runtime", "expected", "duplicate", "missing", "unknown", "source"]
)
def test_stale_or_unresolved_evidence_fails_closed(tmp_path: Path, mutation: str) -> None:
    root, case, node = evidence_root(tmp_path)
    path = report(root, case, node)
    if mutation == "runtime":
        (root / "src/changed.py").write_text("changed = True\n")
    elif mutation == "expected":
        data = json.loads((root / FIXTURE).read_text())
        data["cases"][0]["expected"] = {"success": "incorrect"}
        (root / FIXTURE).write_text(json.dumps(data))
    elif mutation in {"duplicate", "missing"}:
        tree = ET.parse(path)
        test = next(tree.getroot().iter("testcase"))
        if mutation == "duplicate":
            tree.getroot().append(test)
        else:
            tree.getroot().remove(test)
        tree.write(path)
    else:
        data = json.loads((root / MANIFEST).read_text())
        if mutation == "unknown":
            data["obligations"][0]["case_ids"] = ["unknown"]
        else:
            data["source_sha256"] = {}
        (root / MANIFEST).write_text(json.dumps(data))
    assert evaluate_execution(root, path).problem("trait:example") is not None


def test_required_status_and_path_only_rows_remain_blocked() -> None:
    result = evaluate(ROOT)
    blocked = {b.identifier for b in result.blockers if b.kind == "execution"}
    assert "capability:gurps.magic.spellcasting" in blocked
    assert any(identifier.startswith("section:") for identifier in blocked)
    assert any(identifier.startswith("supernatural/") for identifier in blocked)
    assert not result.certified


@pytest.mark.parametrize(
    "service,capability_id,identifier",
    [
        ("quick_contest", "gurps.check.quick_contest", "capability:gurps.check.quick_contest"),
        (
            "regular_contest",
            "gurps.check.regular_contest",
            "capability:gurps.check.regular_contest",
        ),
        ("resistance", "gurps.check.resistance", "capability:gurps.check.resistance"),
        ("resistance", "gurps.check.resistance", "section:campaigns:b349:the-rule-of-16"),
        (
            "skills",
            "gurps.character.skill_difficulty",
            "capability:gurps.character.skill_difficulty",
        ),
        ("skills", "gurps.character.skill_defaults", "capability:gurps.character.skill_defaults"),
        ("skills", "gurps.character.specialties", "capability:gurps.character.specialties"),
        ("skills", "gurps.character.techniques", "capability:gurps.character.techniques"),
        ("repeated_attempt", "gurps.check.success", "section:campaigns:b348:repeated-attempts"),
        (
            "regular_contest",
            "gurps.check.regular_contest",
            "section:campaigns:b349:regular-contests",
        ),
    ],
)
@pytest.mark.parametrize("last_case_status", [None, "missing", "skipped", "failure", "stale"])
def test_obligation_certification_requires_every_bound_source_case(
    tmp_path: Path, service: str, capability_id: str, identifier: str, last_case_status: str | None
) -> None:
    manifest = json.loads((ROOT / MANIFEST).read_text())
    obligation = next(row for row in manifest["obligations"] if row["identifier"] == identifier)
    case_ids = set(obligation["case_ids"])
    fixture = json.loads((ROOT / FIXTURE).read_text())
    cases = [case for case in fixture["cases"] if case["id"] in case_ids]
    # Include every independent Basic Set case for this service, not just a
    # representative happy path that could hide ties or levelling boundaries.
    assert case_ids == {
        case["id"]
        for case in fixture["cases"]
        if (
            case["id"].startswith("basic-rule-of-16-")
            if identifier == "section:campaigns:b349:the-rule-of-16"
            else case.get("service") == service
        )
        and case["capability_id"] == capability_id
        and case["profile"] == "gurps-basic-set-4e-2004"
    }
    manifest["cases"] = [row for row in manifest["cases"] if row["case_id"] in case_ids]
    manifest["obligations"] = [obligation]
    fixture["cases"] = cases
    for path, data in ((MANIFEST, manifest), (FIXTURE, fixture)):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(json.dumps(data))

    suite = ET.Element("testsuite")
    by_id = {case["id"]: case for case in cases}
    selected_binding = next(
        (
            binding
            for binding in manifest["cases"]
            if binding["case_id"]
            in {"basic-regular-contest-live-pin-rounds", "basic-rule-of-16-live-waiver"}
        ),
        manifest["cases"][-1],
    )
    for binding in manifest["cases"]:
        for node_index, node in enumerate(binding["tests"]):
            last = binding == selected_binding and node_index == len(binding["tests"]) - 1
            if last and last_case_status == "missing":
                continue
            status = (
                last_case_status if last and last_case_status not in ("missing", "stale") else None
            )
            path = report(tmp_path, by_id[binding["case_id"]], node, status=status)
            test = next(ET.parse(path).getroot().iter("testcase"))
            if last and last_case_status == "stale":
                for prop in test.findall("properties/property"):
                    if prop.get("name") == "checkout_digest":
                        prop.set("value", "0" * 64)
            suite.append(test)
    path = tmp_path / "contests.xml"
    ET.ElementTree(suite).write(path)
    result = evaluate_execution(tmp_path, path)
    assert (result.problem(identifier) is None) == (last_case_status is None)
    assert (identifier in result.verified) == (last_case_status is None)
    if identifier.startswith("section:"):
        assert result.problem(f"capability:{capability_id}") is not None


def test_repeated_attempt_cases_cover_the_exact_reviewed_section() -> None:
    manifest = json.loads((ROOT / MANIFEST).read_text())
    identifier = "section:campaigns:b348:repeated-attempts"
    obligation = next(row for row in manifest["obligations"] if row["identifier"] == identifier)
    cases = {case["id"]: case for case in json.loads((ROOT / FIXTURE).read_text())["cases"]}
    assert {cases[key]["input"]["policy"] for key in obligation["case_ids"]} == {
        "single-chance",
        "retry-until-success",
        "unknown-until-later",
        "hazardous-failure",
    }
    assert all(
        cases[key]["reference"] == "B348, Repeated Attempts"
        and cases[key]["source_id"] == "sjg:basic-set-campaigns-4e-2004"
        for key in obligation["case_ids"]
    )
    ledger = json.loads(
        (ROOT / "src/wayfarer/certification/basic_set_audit/sections.json").read_text()
    )
    row = next(row for row in ledger["rows"] if row["id"] == identifier)
    assert row["row_kind"] == "mechanic" and row["disposition"] == "required"
    assert row["source_review"] == "reviewed" and row["printed_page"] == 348


def test_regular_contest_section_requires_exact_source_and_both_live_backends() -> None:
    manifest = json.loads((ROOT / MANIFEST).read_text())
    identifier = "section:campaigns:b349:regular-contests"
    obligation = next(row for row in manifest["obligations"] if row["identifier"] == identifier)
    capability = next(
        row
        for row in manifest["obligations"]
        if row["identifier"] == "capability:gurps.check.regular_contest"
    )
    assert obligation["case_ids"] == capability["case_ids"]
    cases = {case["id"]: case for case in json.loads((ROOT / FIXTURE).read_text())["cases"]}
    assert all(
        cases[key]["reference"].startswith("B349, Regular Contests")
        and cases[key]["source_id"] == "sjg:basic-set-campaigns-4e-2004"
        for key in obligation["case_ids"]
    )
    binding = next(
        row
        for row in manifest["cases"]
        if row["case_id"] == "basic-regular-contest-live-pin-rounds"
    )
    assert binding["tests"][-2:] == [
        "tests/test_regular_contest_section.py::test_live_pin_matches_source_rounds_and_reexecutes[sqlite]",
        "tests/test_regular_contest_section.py::test_live_pin_matches_source_rounds_and_reexecutes[postgres]",
    ]
    ledger = json.loads(
        (ROOT / "src/wayfarer/certification/basic_set_audit/sections.json").read_text()
    )
    row = next(row for row in ledger["rows"] if row["id"] == identifier)
    assert row["row_kind"] == "mechanic" and row["disposition"] == "required"
    assert row["source_review"] == "reviewed" and row["printed_page"] == 349
    assert not any(
        row["identifier"] in ("capability:gurps.check.success", "section:campaigns:b348:contests")
        for row in manifest["obligations"]
    )


def test_rule_of_16_section_requires_reviewed_examples_and_both_waiver_backends() -> None:
    manifest = json.loads((ROOT / MANIFEST).read_text())
    identifier = "section:campaigns:b349:the-rule-of-16"
    obligation = next(row for row in manifest["obligations"] if row["identifier"] == identifier)
    assert len(obligation["case_ids"]) == 15
    cases = {case["id"]: case for case in json.loads((ROOT / FIXTURE).read_text())["cases"]}
    assert all(
        cases[key]["reference"].startswith("B349, Rule of 16")
        and cases[key]["source_id"] == "sjg:basic-set-campaigns-4e-2004"
        for key in obligation["case_ids"]
    )
    binding = next(
        row for row in manifest["cases"] if row["case_id"] == "basic-rule-of-16-live-waiver"
    )
    assert binding["tests"] == [
        "tests/test_rule_of_16_waiver.py::test_waived_resistance_retains_offered_cap_and_reexecutes[sqlite]",
        "tests/test_rule_of_16_waiver.py::test_waived_resistance_retains_offered_cap_and_reexecutes[postgres]",
    ]
    ledger = json.loads(
        (ROOT / "src/wayfarer/certification/basic_set_audit/sections.json").read_text()
    )
    row = next(row for row in ledger["rows"] if row["id"] == identifier)
    assert row["row_kind"] == "mechanic" and row["disposition"] == "required"
    assert row["source_review"] == "reviewed" and row["printed_page"] == 349
    assert not any(
        row["identifier"] in ("capability:gurps.check.success", "section:campaigns:b348:contests")
        for row in manifest["obligations"]
    )
