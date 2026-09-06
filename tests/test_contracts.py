"""Contract regression cases independent of unfinished gameplay transports."""

from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.validate_contracts import ROOT, mapping, read, sequence, validate_contract


def payload_validator(name: str) -> Draft202012Validator:
    shared = mapping(read(ROOT / "schemas.json"))
    return Draft202012Validator(
        {"$ref": f"#/$defs/{name}", "$defs": shared["$defs"]}, format_checker=FormatChecker()
    )


def fixture(name: str) -> dict[str, object]:
    examples = sequence(read(ROOT / "examples.json"))
    return deepcopy(
        mapping(next(mapping(item)["value"] for item in examples if mapping(item)["name"] == name))
    )


def test_frozen_contract_and_all_operation_examples() -> None:
    operations, examples = validate_contract(
        mapping(read(ROOT / "openapi.json")),
        mapping(read(ROOT / "schemas.json")),
        sequence(read(ROOT / "examples.json")),
    )
    assert operations >= 17
    assert examples > operations


@pytest.mark.parametrize("field", ["hp", "roll", "principal_id", "role", "effects"])
def test_client_cannot_supply_authoritative_fields(field: str) -> None:
    request = fixture("submitActionRequest")
    request[field] = "forged"
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)
    del request[field]
    mapping(request["intent"])[field] = "forged"
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)


def test_item_use_requires_inventory_precondition() -> None:
    request = fixture("submitActionRequest")
    del mapping(request["expected_versions"])["inventory"]
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)


def test_text_intent_can_omit_inventory_but_not_scene_version() -> None:
    request = fixture("naturalLanguageRequest")
    versions = mapping(request["expected_versions"])
    del versions["inventory"]
    payload_validator("SubmitAction").validate(request)
    del versions["scene"]
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)


@pytest.mark.parametrize("state", ["submitted", "resolving", "rejected", "cancelled"])
def test_non_successful_action_cannot_claim_resolution(state: str) -> None:
    action = fixture("action_succeeded")
    action["status"] = state
    with pytest.raises(ValidationError):
        payload_validator("Action").validate(action)


def test_success_requires_resolution_and_clarification_requires_prompt() -> None:
    action = fixture("action_succeeded")
    del action["resolution"]
    with pytest.raises(ValidationError):
        payload_validator("Action").validate(action)
    action = fixture("action_needs_clarification")
    del action["clarification"]
    with pytest.raises(ValidationError):
        payload_validator("Action").validate(action)


def test_bad_command_id_and_negative_quantity_are_rejected() -> None:
    request = fixture("submitActionRequest")
    request["command_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)
    request = fixture("submitActionRequest")
    mapping(request["intent"])["quantity"] = -1
    with pytest.raises(ValidationError):
        payload_validator("SubmitAction").validate(request)


@pytest.mark.parametrize("role", ["gm", "administrator"])
def test_invitation_cannot_grant_privileged_role(role: str) -> None:
    request = fixture("createInvitationRequest")
    request["role"] = role
    with pytest.raises(ValidationError):
        payload_validator("InvitationRequest").validate(request)


@pytest.mark.parametrize("defect", ["security", "policy", "reference", "example", "error"])
def test_contract_gate_detects_drift(defect: str) -> None:
    spec = mapping(read(ROOT / "openapi.json"))
    shared = mapping(read(ROOT / "schemas.json"))
    examples = sequence(read(ROOT / "examples.json"))
    operation = mapping(mapping(mapping(spec["paths"])["/me"])["get"])
    if defect == "security":
        operation["security"] = []
    elif defect == "policy":
        del operation["x-authorization"]
    elif defect == "reference":
        mapping(shared["$defs"])["Id"] = {"$ref": "#/$defs/Missing"}
    elif defect == "example":
        examples = [item for item in examples if mapping(item)["operation_id"] != "getMe"]
    else:
        error = next(item for item in examples if mapping(item)["name"] == "unauthenticated")
        mapping(mapping(error)["value"])["code"] = "illegal_action"
    with pytest.raises((ValueError, KeyError)):
        validate_contract(spec, shared, examples)
