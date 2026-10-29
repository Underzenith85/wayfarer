"""B348 declarations cannot drop source families on both sides of the join.

Reports here are synthetic join-unit inputs, never behavioral conformance credit.
Actual provider values are independently asserted by the native evidence helper.
"""

import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from wayfarer.certification.executable_evidence import (
    FIXTURE,
    MANIFEST,
    QUICK_CONTEST_OBLIGATION,
    QUICK_CONTEST_REQUIREMENTS,
    QUICK_CONTEST_SHAPES,
    CaseBinding,
    ExecutionManifest,
    ObligationBinding,
    evaluate_execution,
    provenance,
)
from wayfarer.certification.source_ledgers import BASELINE_ID, SOURCE_DIGESTS

# Independently approved source inventory, not selected from the mutable JSONs.
APPROVED_IDS = frozenset(
    {
        "basic-b348-q1-break-free-first-success",
        "basic-b348-q1-break-free-second-success",
        "basic-b348-q2-break-free-fright-aftermath",
        "basic-b348-q3-break-free-both-fail",
        "basic-b348-q4-break-free-success-tie",
        "basic-b348-q4-break-free-failure-tie",
        "basic-b348-q5-break-free-mixed-victory",
        "basic-b348-q6-break-free-critical-margin",
        "basic-b348-r7-ability-burning-malediction-literal-draws",
        "basic-b348-r8-ability-burning-malediction-literal-draws",
        "basic-b348-r9-ability-burning-malediction-literal-draws",
        "basic-b348-r7-ability-mind-reading-literal-draws",
        "basic-b348-r8-ability-mind-reading-literal-draws",
        "basic-b348-r9-ability-mind-reading-literal-draws",
        "basic-b348-r7-creature-gaze",
        "basic-b348-r8-creature-gaze",
        "basic-b348-r9-creature-gaze",
        "basic-b348-r7-mental-mind-reading",
        "basic-b348-r7-mental-mind-probe",
        "basic-b348-r7-mental-mind-control",
        "basic-b348-r7-mental-possession",
        "basic-b348-r7-mental-neutralize",
        "basic-b348-r7-mental-dominance",
        "basic-b348-r7-mental-terror",
        "basic-b348-r7-mental-reading-optional-subject",
        "basic-b348-r8-mental-reading",
        "basic-b348-r9-mental-reading",
    }
)
GRID_PROBLEM = "Quick Contests declaration must contain the exact approved 27-case grid"
FIRST = "basic-b348-q1-break-free-first-success"


def source_cases() -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for identifier, requirement in QUICK_CONTEST_REQUIREMENTS.items():
        inputs, expected = QUICK_CONTEST_SHAPES[requirement.shape]
        result.append(
            {
                **requirement.metadata,
                "id": identifier,
                "reference": "B348",
                "provenance": "Synthetic declaration-unit input, not a behavioral witness",
                "input": {key: 0 for key in inputs},
                "expected": {key: 0 for key in expected},
            }
        )
    return result


def source_bindings() -> list[CaseBinding]:
    return [
        CaseBinding(
            case_id=identifier,
            tests=tuple(
                f"tests/test_declaration_witness.py::{identifier}[{backend}]"
                for backend in (
                    ("sqlite", "postgres")
                    if requirement.provider.startswith("public ")
                    else ("system",)
                )
            ),
        )
        for identifier, requirement in QUICK_CONTEST_REQUIREMENTS.items()
    ]


def write_join(
    root: Path,
    cases: list[dict[str, object]],
    bindings: list[CaseBinding],
    case_ids: tuple[str, ...],
    *,
    identifier: str | None = QUICK_CONTEST_OBLIGATION,
) -> Path:
    manifest = ExecutionManifest(
        schema_version=1,
        baseline_id=BASELINE_ID,
        source_sha256=SOURCE_DIGESTS,
        cases=tuple(bindings),
        obligations=()
        if identifier is None
        else (ObligationBinding(identifier=identifier, case_ids=case_ids),),
    )
    for path in (MANIFEST, FIXTURE):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(manifest.model_dump_json())
    (root / FIXTURE).write_text(json.dumps({"baseline_id": BASELINE_ID, "cases": cases}))
    by_id = {str(case["id"]): case for case in cases}
    suite = ET.Element("testsuite")
    for binding in bindings:
        for node in binding.tests:
            filename, name = node.split("::", 1)
            test = ET.SubElement(
                suite,
                "testcase",
                classname=filename.removesuffix(".py").replace("/", "."),
                name=name,
            )
            properties = ET.SubElement(test, "properties")
            for key, value in provenance(root, by_id[binding.case_id]).items():
                ET.SubElement(properties, "property", name=key, value=value)
    report = root / "report.xml"
    ET.ElementTree(suite).write(report)
    return report


def complete_join(root: Path) -> Path:
    return write_join(root, source_cases(), source_bindings(), tuple(APPROVED_IDS))


def test_literal_declaration_retains_all_nine_source_families_and_actual_providers() -> None:
    assert set(QUICK_CONTEST_REQUIREMENTS) == APPROVED_IDS
    assert len(APPROVED_IDS) == 27
    assert Counter(row.family for row in QUICK_CONTEST_REQUIREMENTS.values()) == {
        "Q1": 2,
        "Q2": 1,
        "Q3": 1,
        "Q4": 2,
        "Q5": 1,
        "Q6": 1,
        "R7": 11,
        "R8": 4,
        "R9": 4,
    }
    assert sum(len(binding.tests) for binding in source_bindings()) == 41
    assert "basic-b348-q2-kernel-positive-modifiers" not in APPROVED_IDS
    assert "exact_seeded_triples" not in QUICK_CONTEST_SHAPES["ordinary"][0]


def test_complete_source_grid_can_join_a_fresh_report(tmp_path: Path) -> None:
    result = evaluate_execution(tmp_path, complete_join(tmp_path))
    assert result.error is None and result.problem(QUICK_CONTEST_OBLIGATION) is None


@pytest.mark.parametrize("omission", ["single-case", "family", "provider"])
def test_self_consistent_omission_from_both_fixture_and_binding_reaches_grid_guard(
    tmp_path: Path, omission: str
) -> None:
    removed = {
        identifier
        for identifier, row in QUICK_CONTEST_REQUIREMENTS.items()
        if (
            identifier == FIRST
            if omission == "single-case"
            else row.family == "R9"
            if omission == "family"
            else row.service == "current_creature_resistance"
        )
    }
    cases = [case for case in source_cases() if case["id"] not in removed]
    bindings = [binding for binding in source_bindings() if binding.case_id not in removed]
    # All surviving case digests, checkout pins and passing node properties are
    # regenerated AFTER the self-consistent mutation. No incidental stale join.
    report = write_join(tmp_path, cases, bindings, tuple(APPROVED_IDS - removed))
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert result.problem(QUICK_CONTEST_OBLIGATION) == GRID_PROBLEM
    assert QUICK_CONTEST_OBLIGATION not in result.verified


@pytest.mark.parametrize(
    "identifier", [None, "section:campaigns:b348:contests", "capability:gurps.check.quick_contest"]
)
def test_absent_child_is_never_inferred_from_parent_or_capability(
    tmp_path: Path, identifier: str | None
) -> None:
    report = write_join(
        tmp_path, source_cases(), source_bindings(), tuple(APPROVED_IDS), identifier=identifier
    )
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert (
        result.problem(QUICK_CONTEST_OBLIGATION)
        == "No explicit executable conformance case binding"
    )


def test_verified_status_words_do_not_declare_the_child(tmp_path: Path) -> None:
    cases = source_cases()
    for case in cases:
        case.update(implementation="verified", source_review="reviewed", status="verified")
    report = write_join(tmp_path, cases, source_bindings(), tuple(APPROVED_IDS), identifier=None)
    assert evaluate_execution(tmp_path, report).problem(QUICK_CONTEST_OBLIGATION) is not None


@pytest.mark.parametrize(
    "field", ["family", "provider", "operation", "service", "capability_id", "profile", "source_id"]
)
def test_fresh_provenance_cannot_legitimize_changed_source_or_provider_role(
    tmp_path: Path, field: str
) -> None:
    cases = source_cases()
    next(case for case in cases if case["id"] == FIRST)[field] = "wrong-role"
    report = write_join(tmp_path, cases, source_bindings(), tuple(APPROVED_IDS))
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert (
        result.problem(QUICK_CONTEST_OBLIGATION)
        == f"Quick Contests source/provider role changed: {FIRST}"
    )


@pytest.mark.parametrize("field", ["input", "expected"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "planning-placeholder"])
def test_closed_observation_contract_cannot_drop_or_ignore_fields(
    tmp_path: Path, field: str, mutation: str
) -> None:
    cases = source_cases()
    case = next(case for case in cases if case["id"] == FIRST)
    value = case[field]
    assert isinstance(value, dict)
    if mutation == "missing":
        value.pop(next(iter(value)))
    else:
        value["exact_seeded_triples" if mutation == "planning-placeholder" else "ignored"] = 0
    report = write_join(tmp_path, cases, source_bindings(), tuple(APPROVED_IDS))
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert (
        result.problem(QUICK_CONTEST_OBLIGATION)
        == f"Quick Contests closed observation shape changed: {FIRST}"
    )


@pytest.mark.parametrize("mutation", ["duplicate", "optional-kernel", "unknown"])
def test_duplicate_or_substituted_declared_cases_fail_before_join(
    tmp_path: Path, mutation: str
) -> None:
    identifiers = tuple(APPROVED_IDS)
    if mutation == "duplicate":
        identifiers += (FIRST,)
    else:
        identifiers = tuple(x for x in identifiers if x != FIRST) + (
            "basic-b348-q2-kernel-positive-modifiers"
            if mutation == "optional-kernel"
            else "unknown",
        )
    result = evaluate_execution(
        tmp_path, write_join(tmp_path, source_cases(), source_bindings(), identifiers)
    )
    assert result.error is None
    assert result.problem(QUICK_CONTEST_OBLIGATION) == (
        "Quick Contests declaration repeats a required case"
        if mutation == "duplicate"
        else GRID_PROBLEM
    )


@pytest.mark.parametrize(
    "mutation",
    ["public-one", "public-duplicate", "public-no-backend", "system-two", "system-database"],
)
def test_backend_binding_contract_does_not_invent_or_omit_hosts(
    tmp_path: Path, mutation: str
) -> None:
    bindings = source_bindings()
    index = (
        0
        if mutation.startswith("public")
        else next(i for i, b in enumerate(bindings) if "creature" in b.case_id)
    )
    binding = bindings[index]
    if mutation in {"public-one", "system-two"}:
        tests = binding.tests[:1] if mutation == "public-one" else binding.tests * 2
    elif mutation == "public-duplicate":
        tests = (binding.tests[0], binding.tests[0])
    elif mutation == "system-database":
        tests = (binding.tests[0].replace("[system]", "[postgres]"),)
    else:
        tests = tuple(
            node.replace("[sqlite]", "[plain]").replace("[postgres]", "[other]")
            for node in binding.tests
        )
    bindings[index] = binding.model_copy(update={"tests": tests})
    report = write_join(tmp_path, source_cases(), bindings, tuple(APPROVED_IDS))
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert (
        result.problem(QUICK_CONTEST_OBLIGATION)
        == f"Quick Contests provider/backend nodes changed: {binding.case_id}"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-node",
        "duplicate-node",
        "failure",
        "error",
        "skipped",
        "duplicate-property",
        "stale-profile",
        "stale-checkout",
        "stale-case",
    ],
)
def test_full_grid_still_requires_real_unique_current_passing_node_properties(
    tmp_path: Path, mutation: str
) -> None:
    report = complete_join(tmp_path)
    tree = ET.parse(report)
    suite = tree.getroot()
    node = next(suite.iter("testcase"))
    properties = node.find("properties")
    assert properties is not None
    if mutation == "missing-node":
        suite.remove(node)
    elif mutation == "duplicate-node":
        suite.append(node)
    elif mutation in {"failure", "error", "skipped"}:
        ET.SubElement(node, mutation)
    elif mutation == "duplicate-property":
        properties.append(list(properties)[0])
    elif mutation == "stale-checkout":
        (tmp_path / "src/unrelated.py").write_text("changed = True\n")
    else:
        key = "profile_digest" if mutation == "stale-profile" else "conformance_case_digest"
        next(p for p in properties if p.get("name") == key).set("value", "0" * 64)
    tree.write(report)
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    problem = result.problem(QUICK_CONTEST_OBLIGATION)
    assert problem is not None and "Quick Contests" not in problem
    assert (
        "did not pass"
        if mutation in {"failure", "error", "skipped"}
        else "Missing or duplicate"
        if mutation in {"missing-node", "duplicate-node"}
        else "provenance"
    ) in problem


@pytest.mark.parametrize("mutation", ["source-pins", "baseline"])
def test_required_grid_does_not_bypass_pinned_source_identity(
    tmp_path: Path, mutation: str
) -> None:
    report = complete_join(tmp_path)
    path = tmp_path / MANIFEST
    manifest = ExecutionManifest.model_validate_json(path.read_text())
    manifest = manifest.model_copy(
        update={"source_sha256": {}} if mutation == "source-pins" else {"baseline_id": "wrong"}
    )
    path.write_text(manifest.model_dump_json())
    result = evaluate_execution(tmp_path, report)
    assert result.error is not None and "source pins" in result.error


def test_declared_grid_requires_every_fixture_and_binding(tmp_path: Path) -> None:
    # Retain the approved child declaration but remove the corresponding row on
    # both sides. Surviving report properties are freshly generated.
    cases = [case for case in source_cases() if case["id"] != FIRST]
    bindings = [binding for binding in source_bindings() if binding.case_id != FIRST]
    result = evaluate_execution(
        tmp_path, write_join(tmp_path, cases, bindings, tuple(APPROVED_IDS))
    )
    assert result.error is None
    assert result.problem(QUICK_CONTEST_OBLIGATION) == (
        f"Quick Contests declaration lacks fixture or binding: {FIRST}"
    )


@pytest.mark.parametrize("field", ["input", "expected"])
def test_closed_observations_require_actual_objects(tmp_path: Path, field: str) -> None:
    cases = source_cases()
    next(case for case in cases if case["id"] == FIRST)[field] = []
    report = write_join(tmp_path, cases, source_bindings(), tuple(APPROVED_IDS))
    result = evaluate_execution(tmp_path, report)
    assert result.error is None
    assert result.problem(QUICK_CONTEST_OBLIGATION) == (
        f"Quick Contests closed observation shape changed: {FIRST}"
    )
