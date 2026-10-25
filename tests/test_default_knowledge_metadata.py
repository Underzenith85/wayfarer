"""Independent private B173 policy never rewrites original principal or intent bytes."""

import json

import pytest

from wayfarer.errors import ValidationError
from wayfarer.persistence.command_inputs import (
    DEFAULT_KNOWLEDGE_KEY,
    canonical,
    default_knowledge_generation,
    replay_payload,
    same_input,
    stamp,
    stamp_default_knowledge,
)
from wayfarer.persistence.events import CommandInput, payload_digest


def record(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


@pytest.mark.parametrize(
    "original",
    [
        '{ "principal_id": "director:alice", "command": {"actor_id":"a"} }',
        "director:alice:opaque-original",
        "  [1, 2]  ",
    ],
)
def test_exact_original_bytes_and_principal_survive_private_policy_capture(original: str) -> None:
    prior = stamp(original, version=3)
    assert not default_knowledge_generation(record(prior))
    current = stamp_default_knowledge(prior)
    assert default_knowledge_generation(record(current))
    assert same_input(record(current), original)
    assert not same_input(record(current), original + " ")
    assert replay_payload(current) == (
        json.loads(original) if original.lstrip().startswith(("{", "[")) else original
    )


@pytest.mark.parametrize("bad", [True, False, 0, 3, 1.0, "1", None])
def test_recorded_generation_is_strict_and_never_accepts_a_future_policy(bad: object) -> None:
    payload = json.loads(stamp('{"operation":"combat"}', version=3))
    payload[DEFAULT_KNOWLEDGE_KEY] = bad
    with pytest.raises(ValidationError, match="Unsupported recorded default knowledge"):
        default_knowledge_generation(record(canonical(payload)))


def test_policy_requires_validated_metadata_digest_and_original_identity() -> None:
    with pytest.raises(ValidationError, match="requires command metadata"):
        default_knowledge_generation(record(canonical({DEFAULT_KNOWLEDGE_KEY: 1})))
    text = stamp_default_knowledge(stamp('{"operation":"combat"}', version=3))
    with pytest.raises(ValidationError, match="digest"):
        default_knowledge_generation(CommandInput("0" * 64, text))
    payload = json.loads(text)
    payload["operation"] = "changed"
    with pytest.raises(ValidationError, match="original"):
        default_knowledge_generation(record(canonical(payload)))
