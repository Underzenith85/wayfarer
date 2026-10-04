"""Private buckler consequences capture only from authenticated combat inputs."""

import json
from pathlib import Path

import pytest
from support.runtime import open_store, seed_campaign
from test_command_input_identity import advance, intent
from test_resources import campaign, engine

from wayfarer.engine.simulation.combat.generations import (
    combat_generation,
    paralyze_buckler_drop_enabled,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat.generations import KEY, features
from wayfarer.persistence.command_inputs import same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest


def record(payload: dict[str, object]) -> CommandInput:
    text = stamp(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return CommandInput(payload_digest({"input": text}), text)


def test_absence_and_nested_replay_scope_remain_legacy() -> None:
    old = features(record({"operation": "combat"}))
    new = features(record({"operation": "combat", KEY: ["paralyze-buckler-drop"]}))
    assert not paralyze_buckler_drop_enabled()
    with combat_generation(new):
        assert paralyze_buckler_drop_enabled()
        with combat_generation(old):
            assert not paralyze_buckler_drop_enabled()
        assert paralyze_buckler_drop_enabled()
    assert not paralyze_buckler_drop_enabled()


@pytest.mark.parametrize(
    "bad",
    [
        [True],
        ["paralyze-buckler-drop"] * 2,
        "paralyze-buckler-drop",
        ["paralyze-buckler-drop", "unknown"],
    ],
)
def test_rehashed_invalid_feature_is_rejected(bad: object) -> None:
    value = record({"operation": "combat", KEY: bad})
    with pytest.raises(ValidationError, match="generation"):
        features(value)
    with pytest.raises(ValidationError, match="combat feature"):
        same_input(value, value.text or "")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_feature_retains_public_exact_retry_identity(
    tmp_path: Path, backend: str
) -> None:
    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    payload = intent() | {"operation": "combat"}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    saved = record(payload | {KEY: ["paralyze-buckler-drop"]})
    await store.commit_turn(cid, "same", 0, saved.text or "", advance, actor_id="a")
    before = await store.read(cid)
    history = await store.history(cid)
    assert await store.duplicate(cid, "same", raw) == before
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, "same", raw + " ")
    assert await store.history(cid) == history
    assert await store.read(cid) == before == await store.replay(cid)
