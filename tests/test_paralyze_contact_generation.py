"""Structural private-contact validation, separate from actual buckler host proof."""

import json

import pytest
from pydantic import ValidationError

from wayfarer.engine.simulation.magic.limb_spell_state import ParalyzeLimbContact

LEGACY_JSON = (
    '{"pending_id":"pending","command_id":"attack","encounter_id":"fight",'
    '"attacker_id":"a","defender_id":"b","cast_id":"cast",'
    '"charge_event_id":"event","charge_digest":"digest",'
    '"carrier_item_id":"staff","mode_id":"thrust","energy":3}'
)


def test_legacy_contact_bytes_and_explicit_two_serialize_identically() -> None:
    absent = ParalyzeLimbContact.model_validate_json(LEGACY_JSON)
    explicit = ParalyzeLimbContact.model_validate_json(
        json.dumps({**json.loads(LEGACY_JSON), "generation": 2})
    )
    assert absent.generation == explicit.generation == 2
    assert absent.model_dump_json() == explicit.model_dump_json() == LEGACY_JSON


def test_new_contact_generation_survives_private_receipt_roundtrip() -> None:
    captured = ParalyzeLimbContact.model_validate_json(
        json.dumps({**json.loads(LEGACY_JSON), "generation": 4})
    )
    encoded = captured.model_dump_json()
    assert json.loads(encoded)["generation"] == 4
    assert ParalyzeLimbContact.model_validate_json(encoded) == captured


@pytest.mark.parametrize("generation", [True, False, 2.0, 4.0, "2", "4", 3, 1, 5, None])
def test_contact_generation_refuses_coercion_and_unknown_values(generation: object) -> None:
    raw = {**json.loads(LEGACY_JSON), "generation": generation}
    with pytest.raises(ValidationError, match="integer 2 or 4"):
        ParalyzeLimbContact.model_validate(raw)
    with pytest.raises(ValidationError, match="integer 2 or 4"):
        ParalyzeLimbContact.model_validate_json(json.dumps(raw))
