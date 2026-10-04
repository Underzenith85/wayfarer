"""Typed private contact diagnostics; these do not assert actual host acceptance."""

import pytest
from pydantic import ValidationError

from wayfarer.engine.simulation.magic.hand_melee_contact_state import HandContact, HandContactResult


def contact() -> HandContact:
    return HandContact(
        pending_id="unarmed:pending",
        pending_digest="p",
        command_id="attack",
        encounter_id="fight",
        attacker_id="a",
        defender_id="b",
        hand="right-hand",
        cast_id="charged",
        charge_event_id="charge",
        charge_digest="c",
        build_revision="build",
        body_digest="body",
        mana_event_id="mana",
        mana_event_digest="m",
        energy=2,
    )


@pytest.mark.parametrize("generation", [True, False, 1.0, "1", 2, 3, None])
def test_private_contact_generation_accepts_only_exact_integer_one(generation: object) -> None:
    value = contact().model_dump(mode="json")
    with pytest.raises(ValidationError, match="exact integer 1"):
        HandContact.model_validate(value | {"generation": generation})


def test_ephemeral_validation_flag_cannot_be_persisted_on_contact() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        HandContact.model_validate(contact().model_dump(mode="json") | {"prevalidated": True})
    assert HandContact.model_validate_json(contact().model_dump_json()) == contact()


@pytest.mark.parametrize("generation", [True, 1.0, "1", 4])
def test_result_generation_and_prevalidation_are_not_authorable(generation: object) -> None:
    value = HandContactResult(
        pending_id="unarmed:pending",
        contact_digest="c",
        cast_id="charged",
        attacker_id="a",
        defender_id="b",
        hand="right-hand",
        status="held",
        outcome="held",
        triggered=False,
        ordinary_hit=False,
        actual_defense="dodge",
        defense_check=None,
        defense_implement_id=None,
        location="torso",
        hp_before=10,
        hp_after=10,
    ).model_dump(mode="json")
    with pytest.raises(ValidationError, match="exact integer 1"):
        HandContactResult.model_validate(value | {"generation": generation})
    with pytest.raises(ValidationError, match="Extra inputs"):
        HandContactResult.model_validate(value | {"prevalidated": True})
