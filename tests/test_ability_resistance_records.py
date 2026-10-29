"""Independent private B348 generation keeps exact public and old input bytes."""

import json

import pytest

from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration.ability_resistance_generations import payload, version
from wayfarer.orchestration.ability_rule16_generations import payload as scope_payload
from wayfarer.persistence.ability_resistance_inputs import KEY, ORIGINAL, intent
from wayfarer.persistence.command_inputs import same_input
from wayfarer.persistence.command_inputs import stamp as stamp_symptoms
from wayfarer.persistence.events import CommandInput, payload_digest


def command() -> AbilityCommand:
    return AbilityCommand(
        id="begin", actor_id="a", expected_revision=2, ability_id="advantage:mind-reading"
    )


def saved(raw: str) -> CommandInput:
    return CommandInput(payload_digest({"input": raw}), raw)


@pytest.mark.parametrize("scope", [False, True])
def test_exact_original_principal_and_other_independent_scope(scope: bool) -> None:
    principal = "player:alice"
    value = command()
    base = scope_payload(principal, value, scope)
    assert payload(base, principal, value, False) == base
    assert not version(saved(base))
    encoded = payload(base, principal, value, True)
    assert intent(encoded) == base and version(saved(encoded))
    assert same_input(saved(encoded), base)
    assert same_input(saved(stamp_symptoms(encoded, version=3)), base)
    assert not same_input(saved(encoded), base + " ")
    assert not same_input(saved(encoded), base.replace('"begin"', '"changed"'))
    assert json.loads(encoded)["principal_id"] == principal


@pytest.mark.parametrize("invalid", [True, 1.0, 0, 2, "1", None])
def test_future_and_coerced_generations_refuse(invalid: object) -> None:
    value = command()
    base = scope_payload("a", value, True)
    data = json.loads(payload(base, "a", value, True))
    data[KEY] = invalid
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValidationError):
        version(saved(text))
    with pytest.raises(ValidationError):
        same_input(saved(text), base)


@pytest.mark.parametrize("key", [KEY, ORIGINAL])
def test_duplicate_metadata_cannot_be_hidden_by_outer_stamp(key: str) -> None:
    value = command()
    base = scope_payload("a", value, True)
    encoded = payload(base, "a", value, True)
    data = json.loads(encoded)
    duplicate = "{" + json.dumps(key) + ":" + json.dumps(data[key]) + "," + encoded[1:]
    with pytest.raises(ValidationError):
        same_input(saved(stamp_symptoms(duplicate, version=3)), base)


def test_digest_and_typed_command_cannot_be_forged_with_a_matching_original() -> None:
    value = command()
    base = scope_payload("a", value, True)
    encoded = payload(base, "a", value, True)
    with pytest.raises(ValidationError, match="input changed"):
        version(CommandInput("0" * 64, encoded))
    source = json.loads(base)
    source["command"]["expected_revision"] = True
    raw = json.dumps(source, sort_keys=True, separators=(",", ":"))
    data = json.loads(encoded)
    data["command"] = source["command"]
    data[ORIGINAL] = raw
    with pytest.raises(ValidationError, match="command"):
        version(saved(json.dumps(data, sort_keys=True, separators=(",", ":"))))


def test_old_native_ability_event_remains_exact_original_bytes() -> None:
    import hashlib

    from test_abilities import context, resolve, spec

    from wayfarer.engine.simulation.ability_state import history

    ability = spec("mind-reading")
    state, _, _ = resolve(ability, context(ability), [3, 3, 3, 4, 4, 4])
    # Captured from the unchanged pre-B348 native producer; no new private fields.
    encoded = history(state)[-1][1].model_dump_json()
    assert hashlib.sha256(encoded.encode()).hexdigest() == (
        "53b7f447ac79ed9ecc88be68b06bacdc752bae220af7d42c2c5985d5731bebf4"
    )
