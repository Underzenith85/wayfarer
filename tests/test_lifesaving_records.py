"""Strict private B345 care policy, exact original intent and no public overrides."""

import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareCommand
from wayfarer.orchestration.hazard_care_generations import read
from wayfarer.persistence.command_inputs import same_input
from wayfarer.persistence.command_inputs import stamp as stamp_symptoms
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.hazard_care_inputs import CONTEXT, KEY, ORIGINAL, intent, stamp


def public() -> str:
    return json.dumps(
        {
            "hazard-care": HazardCareCommand(
                id="rescue", actor_id="a", expected_revision=2, treatment_id="bank"
            ).model_dump(mode="json")
        },
        sort_keys=True,
    )


def saved(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


def policy() -> dict[str, object]:
    return validation.mapping(
        json.loads(
            stamp(public(), asdict(HazardCare("lifesaving", "b", "exposure", safe_landing=True)))
        )
    )


@pytest.mark.parametrize("invalid", [True, 1.0, 0, 2, "1", None])
def test_generation_is_an_exact_independent_integer(invalid: object) -> None:
    data = policy()
    data[KEY] = invalid
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError):
        read(saved(text))
    with pytest.raises(ValidationError):
        same_input(saved(stamp_symptoms(text, version=3)), public())


@pytest.mark.parametrize("field", [KEY, CONTEXT, ORIGINAL])
@pytest.mark.parametrize("outer", [False, True])
def test_duplicate_private_keys_refuse_before_outer_metadata_unwrap(
    field: str, outer: bool
) -> None:
    data = policy()
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    duplicate = "{" + json.dumps(field) + ":" + json.dumps(data[field]) + "," + text[1:]
    text = stamp_symptoms(duplicate, version=3) if outer else duplicate
    with pytest.raises(ValidationError):
        same_input(saved(text), public())


@pytest.mark.parametrize(
    "change", ["unknown", "operation", "missing", "original", "actor", "command-type"]
)
def test_private_envelope_and_original_command_binding_refuse_forgery(change: str) -> None:
    data = policy()
    if change == "unknown":
        data["future_private_field"] = 1
    elif change == "operation":
        data["operation"] = "combat"
    elif change == "missing":
        del data[CONTEXT]
    elif change == "original":
        data[ORIGINAL] = public() + " "
        # Alternate original bytes are retained, not a forged equivalence.
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        assert not same_input(saved(text), public())
        return
    else:
        command = data["command"]
        assert isinstance(command, dict)
        command["actor_id" if change == "actor" else "expected_revision"] = (
            "b" if change == "actor" else True
        )
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError):
        read(saved(text))


@pytest.mark.parametrize(
    "field,value",
    [("safe_landing", 1), ("technology_level", True), ("kind", "unknown"), ("future_field", 0)],
)
def test_context_schema_does_not_coerce_or_ignore_fields(field: str, value: object) -> None:
    data = policy()
    context = data[CONTEXT]
    assert isinstance(context, dict)
    context[field] = value
    with pytest.raises(ValidationError):
        read(saved(json.dumps(data, sort_keys=True, separators=(",", ":"))))


def test_exact_bytes_legacy_absence_digest_and_public_boundary() -> None:
    raw = public()
    encoded = stamp(raw, asdict(HazardCare("lifesaving", "b", "exposure", safe_landing=True)))
    assert intent(encoded) == raw and same_input(saved(encoded), raw)
    assert not same_input(saved(encoded), raw + " ")
    assert not same_input(saved(encoded), raw.replace('"bank"', '"different"'))
    assert read(saved(encoded)).current and not read(saved(raw)).current
    with pytest.raises(ValidationError, match="input changed"):
        read(CommandInput("0" * 64, encoded))
    with pytest.raises(ValidationError):
        read(saved(encoded + " "))
    with pytest.raises(SchemaError):
        HazardCareCommand.model_validate(
            {
                "id": "rescue",
                "actor_id": "a",
                "expected_revision": 0,
                "treatment_id": "bank",
                KEY: 1,
            }
        )


def test_captured_public_command_types_remain_strict_with_matching_original() -> None:
    data = policy()
    command = data["command"]
    assert isinstance(command, dict)
    command["expected_revision"] = True
    data[ORIGINAL] = json.dumps({"hazard-care": command}, sort_keys=True)
    with pytest.raises(ValidationError, match="captured hazard care command"):
        read(saved(json.dumps(data, sort_keys=True, separators=(",", ":"))))
