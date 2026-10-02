"""Private reaction generations preserve original identity and old replay inputs."""

import json
from pathlib import Path

import pytest
from support.runtime import open_store, seed_campaign
from test_reaction_task_host import fixture
from test_resources import campaign, engine

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.errors import ConflictError, ValidationError
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
from wayfarer.persistence.command_inputs import generation, same_input, stamp
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
    assert generation(record(stored))
    assert replay_payload(stored) == json.loads(original)
    assert same_input(record(stored), stamp(encoded))
    assert same_input(record(stored), original)
    # A request whose original byte identity changes is a different command.
    other, _ = await capture(play.store, cid, "new", original.replace("  ", " "))
    assert not same_input(record(stored), stamp(other))
    assert not same_input(record(stored), original.replace("  ", " "))
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
    with pytest.raises(ValidationError):
        same_input(record(encoded), encoded)
    with pytest.raises(ValidationError):
        same_input(record(stamp("{}")), encoded)


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
    with pytest.raises(ValidationError, match="digest"):
        same_input(CommandInput("0" * 64, stamp("{}")), "{}")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("style", ["legacy", "symptoms", "reaction", "nested"])
@pytest.mark.parametrize(
    "original", ['{\n  "id": "command"\n}', 'a:{"id":"command"}', '["command", "intent"]']
)
async def test_early_store_retry_preserves_each_recorded_layer_and_raw_bytes(
    tmp_path: Path, backend: str, style: str, original: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    encoded = original
    if style in ("reaction", "nested"):
        encoded, correct = await capture(store, cid, "command", original)
        assert correct
    if style in ("symptoms", "nested"):
        encoded = stamp(encoded)

    def resolve(current: Campaign) -> CommandReceipt:
        current["revision"] += 1
        return CommandReceipt(action="resource", outcome="recorded-input")

    await store.commit_turn(cid, "command", 0, encoded, resolve, actor_id="a")
    saved = await store.read(cid)
    history = await store.history(cid)
    retained = await store.command_input(cid, "command")
    assert retained is not None and retained.text == encoded
    assert generation(retained) is (style in ("symptoms", "nested"))
    assert _generation(retained) is (style in ("reaction", "nested"))
    assert await store.duplicate(cid, "command", original) == saved
    assert await store.duplicate(cid, "command", encoded) == saved
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, "command", original.replace("command", "changed"))
    if original.startswith("{"):
        with pytest.raises(ConflictError, match="different input"):
            await store.duplicate(cid, "command", original.replace("  ", " "))
    assert await store.read(cid) == saved and await store.history(cid) == history
