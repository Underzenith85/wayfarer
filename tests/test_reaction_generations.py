"""Private reaction generations preserve original identity and old replay inputs."""

import json
from pathlib import Path

import pytest
from test_reaction_task_host import fixture

from wayfarer.errors import ValidationError
from wayfarer.orchestration.social_generations import (
    KEY,
    ORIGINAL,
    WRAPPED,
    _generation,
    capture,
    correct_social_reactions,
    replay_payload,
    social_generation,
)
from wayfarer.persistence.command_inputs import same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest


def record(text: str) -> CommandInput:
    return CommandInput(payload_digest({"input": text}), text)


async def test_byte_exact_nested_generation_roundtrip(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    original = '{\n  "operation": "example", "command": {"kind": "example"}\n}'
    encoded, correct = await capture(play.store, cid, "new", original)
    assert correct and json.loads(encoded)[ORIGINAL] == original
    stored = stamp(encoded)
    assert _generation(record(stored))
    assert replay_payload(stored) == json.loads(original)
    assert same_input(record(stored), stamp(encoded))
    # A request whose original byte identity changes is a different command.
    other, _ = await capture(play.store, cid, "new", original.replace("  ", " "))
    assert not same_input(record(stored), stamp(other))
    assert not _generation(record(original))
    assert not _generation(record(stamp(original)))
    assert replay_payload(stamp(original)) == json.loads(original)


@pytest.mark.parametrize(
    "value",
    [
        {KEY: True, ORIGINAL: "{}"},
        {KEY: 2, ORIGINAL: "{}"},
        {KEY: 1, ORIGINAL: "{}", "extra": 1},
        {KEY: 1, ORIGINAL: '{"reaction_semantics_generation":1}'},
        {KEY: 1, WRAPPED: "{}"},
        {ORIGINAL: "{}"},
        {KEY: 1, WRAPPED: "not-json", "extra": 1},
    ],
)
def test_malformed_or_spoofed_generation_fails_closed(value: dict[str, object]) -> None:
    encoded = stamp(json.dumps(value))
    with pytest.raises(ValidationError):
        replay_payload(encoded)


def test_generation_context_resets_after_nested_failure() -> None:
    assert correct_social_reactions()
    with social_generation(False):
        assert not correct_social_reactions()
        with pytest.raises(RuntimeError):
            with social_generation(True):
                assert correct_social_reactions()
                raise RuntimeError("stop")
        assert not correct_social_reactions()
    assert correct_social_reactions()


def test_generation_digest_mismatch_refuses() -> None:
    with pytest.raises(ValidationError, match="digest"):
        _generation(CommandInput("0" * 64, "{}"))
