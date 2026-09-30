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
