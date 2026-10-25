"""Diagnosis version2 is independent of every accepted version1 care context."""

import json
from dataclasses import asdict

import pytest

from wayfarer.engine.rules.types.hazard_care import HazardCare, diagnosis_target
from wayfarer.errors import ValidationError
from wayfarer.orchestration.hazard_care import HazardCareCommand
from wayfarer.orchestration.hazard_care_generations import payload, read
from wayfarer.persistence.command_inputs import same_input
from wayfarer.persistence.command_inputs import stamp as symptoms
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.hazard_care_inputs import CONTEXT, KEY, ORIGINAL, intent, stamp


def public() -> str:
    return json.dumps(
        {
            "hazard-care": HazardCareCommand(
                id="exam", actor_id="a", expected_revision=1, treatment_id="symptoms"
            ).model_dump(mode="json")
        },
        sort_keys=True,
    )


def record(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


def canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def diagnosis(version: int = 2) -> str:
    return stamp(
        public(),
        asdict(HazardCare("diagnosis", "b", "exposure", technology_level=8)),
        version=version,
    )


@pytest.mark.parametrize("kind", ["lifesaving", "diagnosis", "antibiotics"])
def test_existing_one_captures_context_but_never_implies_diagnosis_floor(kind: str) -> None:
    data = asdict(HazardCare("diagnosis", "b", "exposure", technology_level=8))
    data["kind"] = kind
    old = stamp(public(), data)
    selected = read(record(old))
    assert (
        selected.current and selected.saved and selected.context and not selected.diagnosis_minimum
    )
    assert intent(old) == public() and same_input(record(old), public())
    assert json.loads(old)[KEY] == 1


def test_current_diagnosis_two_has_independent_bit_and_exact_original() -> None:
    text = diagnosis()
    selected = read(record(text))
    assert (
        selected.current
        and selected.diagnosis_minimum
        and selected.context
        and selected.context.kind == "diagnosis"
    )
    assert intent(text) == public() and same_input(record(text), public())
    assert not same_input(record(text), public() + " ")
    assert payload(public(), selected.context, current=True, diagnosis_minimum=True) == text
    assert not read(record(public())).current and not read(record(public())).diagnosis_minimum


@pytest.mark.parametrize("kind", ["lifesaving", "antibiotics"])
def test_two_cannot_reinterpret_other_care_kinds(kind: str) -> None:
    data = json.loads(diagnosis())
    data[CONTEXT]["kind"] = kind
    with pytest.raises(ValidationError, match="exact context"):
        read(record(canonical(data)))


@pytest.mark.parametrize("version", [True, 2.0, "2", None, 0, 3, -1])
def test_diagnosis_generation_uses_strict_exact_versions(version: object) -> None:
    data = json.loads(diagnosis())
    data[KEY] = version
    with pytest.raises(ValidationError):
        same_input(record(canonical(data)), public())


@pytest.mark.parametrize("key", [KEY, CONTEXT, ORIGINAL])
@pytest.mark.parametrize("outer", [False, True])
def test_diagnosis_two_private_duplicates_are_rejected_before_unwrap(key: str, outer: bool) -> None:
    text = diagnosis()
    if outer:
        text = symptoms(text, version=3)
    data = json.loads(text)
    fragment = json.dumps(key) + ":" + canonical(data[key])
    malformed = text.replace(fragment, fragment + "," + fragment)
    assert malformed != text
    with pytest.raises(ValidationError, match="Duplicate"):
        same_input(record(malformed), public())


def test_unbound_negative_policy_never_becomes_diagnosis_floor() -> None:
    with pytest.raises(ValidationError, match="captured exact context"):
        payload(public(), None, current=False, diagnosis_minimum=True)
    assert payload(public(), None, current=False) == public()
    assert diagnosis_target(-2, -2, current=False) == 1
    assert diagnosis_target(4, -1, current=True) == 4
    for base, modifier in [(1, 0), (2, 0), (4, -2)]:
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            diagnosis_target(base, modifier, current=True)
