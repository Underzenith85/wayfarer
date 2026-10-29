"""Evidence-join unit controls, never emitted as native execution witnesses."""

import json
from copy import deepcopy
from typing import cast

import pytest
from support.quick_contest_evidence import ROOT, record_quick_contest

from wayfarer.certification.executable_evidence import FIXTURE, case_digest

CASE = "basic-b348-r7-ability-burning-malediction-literal-draws"


def fixture() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    cases: list[dict[str, object]] = json.loads((ROOT / FIXTURE).read_text())["cases"]
    case = next(c for c in cases if c["id"] == CASE)
    return (
        case,
        deepcopy(cast(dict[str, object], case["input"])),
        deepcopy(cast(dict[str, object], case["expected"])),
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("input_value", "actual input differs"),
        ("result_value", "actual result differs"),
        ("input_bool_is_not_int", "exact JSON types"),
        ("result_bool_is_not_int", "exact JSON types"),
        ("result_float_is_not_int", "exact JSON types"),
        ("missing_input", "actual input keys"),
        ("extra_input", "actual input keys"),
        ("missing_result", "actual result keys"),
        ("extra_result", "actual result keys"),
        ("unbound_kernel", "Unreviewed B348 provider/case"),
    ],
)
def test_mismatched_observations_emit_no_provenance(change: str, reason: str) -> None:
    _, inputs, observed = fixture()
    case_id = CASE
    if change == "input_value":
        inputs["compiled_actor_score"] = 11
    elif change == "result_value":
        observed["target_hp"] = 9
    elif change == "input_bool_is_not_int":
        inputs["rule_of_16"] = 1
    elif change == "result_bool_is_not_int":
        observed["affected"] = 0
    elif change == "result_float_is_not_int":
        observed["contest_draws"] = 3.0
    elif change == "missing_input":
        del inputs["distance_yards"]
    elif change == "extra_input":
        inputs["invented_distance_override"] = 0
    elif change == "missing_result":
        del observed["target_hp"]
    elif change == "extra_result":
        observed["invented_source_claim"] = True
    else:
        assert change == "unbound_kernel"
        case_id = "basic-b348-q2-kernel-positive-modifiers"
    captured: list[tuple[str, object]] = []

    def capture(name: str, value: object) -> None:
        captured.append((name, value))

    with pytest.raises(AssertionError, match=reason):
        record_quick_contest(case_id, inputs, observed, capture)
    assert captured == []


def test_unit_callback_receives_each_fingerprint_once_without_native_junit_claim() -> None:
    # Copied fixture values exercise the join only. This callback is deliberately
    # not pytest's record_property and therefore produces no native JUnit proof.
    case, inputs, observed = fixture()
    captured: list[tuple[str, object]] = []

    def capture(name: str, value: object) -> None:
        captured.append((name, value))

    record_quick_contest(CASE, inputs, observed, capture)
    names = [name for name, _ in captured]
    assert len(names) == len(set(names)) == 5
    assert dict(captured)["conformance_case_id"] == CASE
    assert dict(captured)["conformance_case_digest"] == case_digest(case)
