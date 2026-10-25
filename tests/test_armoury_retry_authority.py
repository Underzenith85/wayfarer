"""Saved Armoury input keeps malformed metadata distinct from denied callers."""

import pytest

from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.armoury_generations import operating_policy, stamp
from wayfarer.persistence.command_inputs import canonical
from wayfarer.persistence.events import CommandInput, payload_digest


def saved_input(principal: object) -> CommandInput:
    original = canonical(
        {
            "operation": "armoury",
            "generation": 1,
            "principal_id": principal,
            "command": AssessRepairParts(
                id="parts", actor_id="b", item_id="repair-target", expected_revision=0
            ).model_dump(mode="json"),
        }
    )
    text = stamp(original)
    return CommandInput(payload_digest({"input": text}), text)


@pytest.mark.parametrize("principal", [None, True, 1, [], {}])
def test_malformed_saved_principal_stays_a_domain_validation_error(principal: object) -> None:
    with pytest.raises(ValidationError):
        operating_policy(saved_input(principal), "parts", "b")


def test_well_formed_saved_principal_distinguishes_same_owner_and_denied_caller() -> None:
    record = saved_input("b")
    assert operating_policy(record, "parts", "b")
    with pytest.raises(AuthorizationError):
        operating_policy(record, "parts", "a")
