"""Initial-only carrier generations cannot impersonate the old bounded Steps."""

import json

import pytest
from test_great_haste_initial_step import initial

from wayfarer.engine.simulation.magic.great_haste_named import HOST_ADAPTER
from wayfarer.errors import ValidationError
from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
from wayfarer.persistence.events import CommandInput, payload_digest


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("generation", [1, 2, 3, 4, 5, 6, 7, 8, True, 6.0])
def test_initial_generation_strict_pairing_and_start_only(named: bool, generation: object) -> None:
    selected = initial(0, named=named)
    command = selected.model_dump(mode="json")
    payload = {
        "operation": "great-haste",
        "generation": generation,
        "principal_id": "alice",
        "command": command,
    }
    payload[ORIGINAL] = json.dumps(payload, sort_keys=True)
    payload[KEY] = 1
    text = json.dumps(payload, sort_keys=True)
    record = CommandInput(payload_digest({"input": text}), text)
    if type(generation) is int and generation == (7 if named else 6):
        assert features(record)
        assert HOST_ADAPTER.validate_json(json.dumps(command)) == selected
    else:
        with pytest.raises(ValidationError, match="generation"):
            features(record)
    command["operation"] = "concentrate"
    with pytest.raises(ValueError):
        HOST_ADAPTER.validate_json(json.dumps(command))


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("metadata", ["absent", "key-only", "original-only", "false", "zero"])
def test_initial_generation_requires_complete_authenticated_metadata(
    named: bool, metadata: str
) -> None:
    payload = {
        "operation": "great-haste",
        "generation": 7 if named else 6,
        "principal_id": "alice",
        "command": initial(0, named=named).model_dump(mode="json"),
    }
    original = json.dumps(payload, sort_keys=True)
    if metadata in ("key-only", "false", "zero"):
        payload[KEY] = False if metadata == "false" else 0 if metadata == "zero" else 1
    if metadata in ("original-only", "false", "zero"):
        payload[ORIGINAL] = original
    text = json.dumps(payload, sort_keys=True)
    with pytest.raises(ValidationError):
        features(CommandInput(payload_digest({"input": text}), text))
