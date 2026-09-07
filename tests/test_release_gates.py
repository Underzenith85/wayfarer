"""The release checker must fail closed even when pytest itself exited successfully."""

import json
from dataclasses import replace
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

import pytest

from scripts import release_gates
from scripts.release_gates import MECHANICS, ROOT, evaluate
from wayfarer.rules.catalog import PROTOTYPE_PACKAGE


@pytest.mark.parametrize("defect", ["none", "missing", "skipped", "failure", "error", "empty"])
def test_release_evidence_rejects_incomplete_or_nonpassing_report(
    tmp_path: Path, defect: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Element("testsuites")
    suite = SubElement(root, "testsuite")
    modules = sorted({m for group in MECHANICS.values() for m in group})
    if defect == "missing":
        modules.remove("test_wave14")
    if defect == "empty":
        modules = []
    for module in modules:
        case = SubElement(suite, "testcase", classname=f"tests.{module}", name="test_example")
        if module == "test_postgres" and defect in ("skipped", "failure", "error"):
            SubElement(case, defect)
    if defect != "empty":
        for node in json.loads((ROOT / "tests/fixtures/release_cases.json").read_text()):
            module, name = node.split("::")
            if defect == "missing" and "test_wave14" in module:
                continue
            SubElement(suite, "testcase", classname=module[:-3].replace("/", "."), name=name)
    path = tmp_path / "junit.xml"
    ElementTree(root).write(path)
    _, errors = evaluate(path)
    assert bool(errors) == (defect != "none")
    if defect == "none":
        output = tmp_path / "release"
        monkeypatch.setattr("sys.argv", ["release_gates", str(path), "--output", str(output)])
        release_gates.main()
        assert json.loads((output / "mechanics.json").read_text())["passed"]
        original = PROTOTYPE_PACKAGE
        changed = replace(original.definitions[0], point_cost=11)
        monkeypatch.setattr(
            release_gates,
            "PROTOTYPE_PACKAGE",
            replace(original, definitions=(changed, *original.definitions[1:])),
        )
        with pytest.raises(SystemExit, match="Approved-source fixture differs"):
            release_gates.main()
        assert not json.loads((output / "mechanics.json").read_text())["passed"]


@pytest.mark.parametrize(
    "defect", ["none", "missing_report", "missing_journey", "empty", "skipped", "failure"]
)
def test_browser_gate_rejects_incomplete_or_nonpassing_journeys(
    tmp_path: Path, defect: str
) -> None:
    from scripts.check_browser_evidence import REQUIRED_JOURNEYS, check

    for report, required in REQUIRED_JOURNEYS.items():
        if report == "reference" and defect == "missing_report":
            continue
        root = Element("testsuite")
        if not (report == "reference" and defect == "empty"):
            names = list(required)
            if report == "reference" and defect == "missing_journey":
                names.pop()
            for index, name in enumerate(names):
                case = SubElement(root, "testcase", name=name)
                if report == "reference" and index == 0 and defect in ("skipped", "failure"):
                    SubElement(case, defect)
        ElementTree(root).write(tmp_path / f"{report}.xml")
    assert bool(check(tmp_path)) == (defect != "none")
