"""Private combat features preserve strict public retry identity in both stores."""

import json
from pathlib import Path

import pytest
from support.runtime import open_store, seed_campaign
from test_command_input_identity import advance, intent
from test_resources import campaign, engine

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.persistence.command_inputs import replay_payload, same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest


def encoded(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("operation", ["combat", "combat-random-unarmed"])
async def test_public_combat_retry_preserves_exact_input_and_snapshot(
    tmp_path: Path, backend: str, operation: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    payload = intent() | {"operation": operation}
    raw = encoded(payload)
    stored = stamp(encoded(payload | {"combat_protocol_features": ["grenade-fuse"]}))
    await store.commit_turn(cid, "same", 0, stored, advance, actor_id="a")
    before = await store.read(cid)
    history = await store.history(cid)
    assert await store.duplicate(cid, "same", raw) == before
    assert await store.duplicate(cid, "same", stored) == before
    for changed in (raw + " ", encoded(payload | {"principal_id": "bob"})):
        with pytest.raises(ConflictError, match="different input"):
            await store.duplicate(cid, "same", changed)
    assert await store.history(cid) == history
    assert await store.read(cid) == before == await store.replay(cid)
    assert replay_payload(stored) == payload | {"combat_protocol_features": ["grenade-fuse"]}


@pytest.mark.parametrize(
    "features", [["future"], ["grenade-fuse", "grenade-fuse"], [True], "grenade-fuse"]
)
def test_invalid_features_reject_even_digest_matching_input(features: object) -> None:
    text = stamp(encoded(intent() | {"combat_protocol_features": features}))
    with pytest.raises(ValidationError, match="combat feature"):
        same_input(CommandInput(payload_digest({"input": text}), text), text)


def test_feature_container_and_digest_are_authenticated() -> None:
    for operation in ("resource", "combat"):
        raw = encoded(intent() | {"operation": operation, "combat_protocol_features": []})
        text = stamp(raw if operation == "resource" else raw + " ")
        with pytest.raises(ValidationError, match="combat feature"):
            same_input(CommandInput(payload_digest({"input": text}), text), text)
    text = stamp(encoded(intent() | {"combat_protocol_features": ["grenade-fuse"]}))
    with pytest.raises(ValidationError, match="digest"):
        same_input(CommandInput("0" * 64, text), encoded(intent()))


@pytest.mark.parametrize("feature", ["grenade-fuse", "maneuver-budget", "acrobatic-trait-bonuses"])
def test_known_features_preserve_public_identity_without_activation(feature: str) -> None:
    text = stamp(encoded(intent() | {"combat_protocol_features": [feature]}))
    assert same_input(CommandInput(payload_digest({"input": text}), text), encoded(intent()))
