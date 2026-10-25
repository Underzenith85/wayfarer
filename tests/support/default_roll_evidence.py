"""Bind actual native B344 results to independent selected-printing expectations."""

import json
from collections.abc import Callable
from pathlib import Path

from wayfarer.certification.executable_evidence import FIXTURE, provenance

ROOT = Path(__file__).resolve().parents[2]


def record_native_default(
    case_id: str,
    inputs: dict[str, object],
    observed: dict[str, object],
    record_property: Callable[[str, object], None],
) -> None:
    cases: list[dict[str, object]] = json.loads((ROOT / FIXTURE).read_text())["cases"]
    case = next(case for case in cases if case["id"] == case_id)
    assert case["input"] == inputs
    assert case["expected"] == observed
    for name, value in provenance(ROOT, case).items():
        record_property(name, value)
