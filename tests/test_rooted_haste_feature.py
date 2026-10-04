"""Private Rooted Haste composition capture only from authenticated combat inputs."""

import json
from pathlib import Path

import pytest
from support.runtime import open_store, seed_campaign
from test_command_input_identity import advance, intent
from test_resources import campaign, engine

from wayfarer.engine.simulation.combat.generations import (
    combat_generation,
    rooted_dodge_haste_composition,
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
    new = features(record({"operation": "combat", KEY: ["rooted-dodge-haste-composition"]}))
    assert not rooted_dodge_haste_composition()
    with combat_generation(new):
        assert rooted_dodge_haste_composition()
        with combat_generation(old):
            assert not rooted_dodge_haste_composition()
        assert rooted_dodge_haste_composition()
    assert not rooted_dodge_haste_composition()


@pytest.mark.parametrize(
    "bad",
    [
        [True],
        ["rooted-dodge-haste-composition"] * 2,
        "rooted-dodge-haste-composition",
        ["rooted-dodge-haste-composition", "unknown"],
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
    saved = record(payload | {KEY: ["rooted-dodge-haste-composition"]})
    await store.commit_turn(cid, "same", 0, saved.text or "", advance, actor_id="a")
    before = await store.read(cid)
    history = await store.history(cid)
    assert await store.duplicate(cid, "same", raw) == before
    with pytest.raises(ConflictError, match="different input"):
        await store.duplicate(cid, "same", raw + " ")
    assert await store.history(cid) == history
    assert await store.read(cid) == before == await store.replay(cid)


def test_exception_restores_legacy_scope_and_public_command_rejects_marker() -> None:
    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.combat.commands import TakeCombatTurn

    with (
        pytest.raises(RuntimeError),
        combat_generation(frozenset({"rooted-dodge-haste-composition"})),
    ):
        assert rooted_dodge_haste_composition()
        raise RuntimeError("refused")
    assert not rooted_dodge_haste_composition()
    with pytest.raises(SchemaError):
        TakeCombatTurn.model_validate(
            {
                "id": "turn",
                "actor_id": "a",
                "expected_revision": 0,
                "encounter_id": "e",
                "maneuver": "do_nothing",
                KEY: ["rooted-dodge-haste-composition"],
            }
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_old_input_capture_stays_absent_despite_current_active_feature(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.combat.generations import capture

    store = open_store(tmp_path, backend=backend)
    initial = campaign(engine())
    cid = initial["id"]
    await seed_campaign(store, initial)
    old = record(intent() | {"operation": "combat"})
    await store.commit_turn(cid, "same", 0, old.text or "", advance, actor_id="a")
    assert await capture(store, cid, "same") == frozenset()
    assert "rooted-dodge-haste-composition" in await capture(store, cid, "fresh")
    assert await store.command_input(cid, "same") == old


def test_health_composition_alone_does_not_activate_haste() -> None:
    with combat_generation(frozenset({"rooted-dodge-health-trait-composition"})):
        assert not rooted_dodge_haste_composition()
    assert not rooted_dodge_haste_composition()


def test_signed_payload_tamper_is_not_a_new_generation() -> None:
    original = record({"operation": "combat", KEY: []})
    changed = record({"operation": "combat", KEY: ["rooted-dodge-haste-composition"]})
    with pytest.raises(ValidationError, match="digest"):
        features(CommandInput(original.payload_hash, changed.text))
