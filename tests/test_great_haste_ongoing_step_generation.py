"""Only exact private ongoing generation pairs can admit the new carrier."""

import json

import pytest
from test_great_haste_ongoing_step import ongoing

from wayfarer.engine.simulation.magic.great_haste_named import HOST_ADAPTER
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
from wayfarer.persistence.events import CommandInput, payload_digest


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("generation", [1, 2, 4, 5, 6, 7, 8, 9, 10, True, 8.0])
def test_ongoing_generation_exact_pair_and_concentrate_only(
    named: bool, generation: object
) -> None:
    command = ongoing(0, named=named).model_dump(mode="json")
    payload = dict(
        operation="great-haste", generation=generation, principal_id="alice", command=command
    )
    payload[ORIGINAL] = json.dumps(payload, sort_keys=True)
    payload[KEY] = 1
    text = json.dumps(payload, sort_keys=True)
    record = CommandInput(payload_digest({"input": text}), text)
    if type(generation) is int and generation == (9 if named else 8):
        assert features(record)
    else:
        with pytest.raises(ValidationError):
            features(record)
    command["operation"] = "start"
    with pytest.raises(ValueError):
        HOST_ADAPTER.validate_json(json.dumps(command))


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize(
    "tamper", ["absent", "key-only", "original-only", "false", "hash", "original-generation"]
)
def test_ongoing_generation_refuses_incomplete_or_changed_caller(named: bool, tamper: str) -> None:
    payload = dict(
        operation="great-haste",
        generation=9 if named else 8,
        principal_id="alice",
        command=ongoing(0, named=named).model_dump(mode="json"),
    )
    original = dict(payload)
    if tamper == "original-generation":
        original["generation"] = 1
    if tamper not in ("absent", "key-only"):
        payload[ORIGINAL] = json.dumps(original, sort_keys=True)
    if tamper not in ("absent", "original-only"):
        payload[KEY] = False if tamper == "false" else 1
    text = json.dumps(payload, sort_keys=True)
    record = CommandInput("0" * 64 if tamper == "hash" else payload_digest({"input": text}), text)
    with pytest.raises(ValidationError):
        features(record)
