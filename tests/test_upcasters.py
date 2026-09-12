"""Retained schemas are executable promises, independent of the current writer."""

import json
from pathlib import Path

import pytest
from test_wave9 import prepare

from wayfarer import contracts, validation
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.events import digest, document
from wayfarer.errors import StorageError
from wayfarer.persistence.events import COMMAND_UPCASTERS, fold
from wayfarer.persistence.upcasters import (
    EVENT_UPCASTERS,
    UpcasterRegistry,
    check_retention,
    read_event,
)

FIXTURE = Path(__file__).parent / "fixtures/retained_schemas.json"


def test_retained_schema_fixtures() -> None:
    data = json.loads(FIXTURE.read_text())
    for key, registry in [("events", EVENT_UPCASTERS), ("commands", COMMAND_UPCASTERS)]:
        for row in data[key]:
            registry.check(row["kind"], row["version"])
    for case in data["folds"]:
        initial = contracts.campaign(validation.decode(case["initial_json"]))
        events = [read_event(json.dumps(event), case["version"]) for event in case["events"]]
        assert digest(document(fold(initial, events))) == case["state_digest"]


def test_structural_upcasters_are_ordered_pure_and_required() -> None:
    def rename(row: dict[str, object]) -> dict[str, object]:
        row["value"] = row.pop("old")
        return row

    def restructure(row: dict[str, object]) -> dict[str, object]:
        row["value"] = {"nested": row["value"]}
        return row

    original: dict[str, object] = {"old": 42}
    registry = UpcasterRegistry(
        {"example": 3}, {("example", 1): rename, ("example", 2): restructure}
    )
    assert registry.read("example", 1, original) == {"value": {"nested": 42}}
    assert original == {"old": 42}
    del registry.steps["example", 1]
    with pytest.raises(StorageError, match="Missing upcaster"):
        registry.read("example", 1, original)
    with pytest.raises(StorageError, match="Unsupported"):
        registry.read("example", 4, original)
    # Added defaulted fields need no JSON migration.
    assert read_event('{"kind":"projection.refresh"}', 1).audience.kind == "campaign"


def test_retirement_requires_all_campaigns_to_have_covering_snapshots() -> None:
    registry = UpcasterRegistry({"example": 2}, {})
    rows: list[dict[str, object]] = [
        {
            "campaign": "a",
            "kind": "example",
            "version": 1,
            "last_revision": 4,
            "snapshot_revision": 4,
        },
        {
            "campaign": "b",
            "kind": "example",
            "version": 1,
            "last_revision": 7,
            "snapshot_revision": 6,
        },
    ]
    with pytest.raises(StorageError, match="Missing upcaster"):
        check_retention(registry, rows)
    rows[1]["snapshot_revision"] = 7
    check_retention(registry, rows)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_adapters_share_upcaster_registry(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend=backend)
    await play.execute(
        cid,
        Wait(id="schema", actor_id="a", expected_revision=0, ticks=1),
        authenticated_actor_id="a",
    )
    expected = await play.store.stream_states(cid)
    calls: list[str] = []

    def migration(row: dict[str, object]) -> dict[str, object]:
        calls.append(str(row["kind"]))
        return row

    monkeypatch.setitem(EVENT_UPCASTERS.current, "state.patched", 2)
    monkeypatch.setitem(EVENT_UPCASTERS.steps, ("state.patched", 1), migration)
    actual = await play.store.stream_states(cid)
    assert [state for state, _ in actual] == [state for state, _ in expected]
    assert [e.event for _, events in actual for e in events] == [
        e.event for _, events in expected for e in events
    ]
    assert "state.patched" in calls
    usage = await play.store.schema_usage()
    assert any(row["campaign"] == cid and row["version"] == 1 for row in usage)
    monkeypatch.delitem(EVENT_UPCASTERS.steps, ("state.patched", 1))
    with pytest.raises(StorageError, match="Missing upcaster"):
        await play.store.stream_states(cid)
    monkeypatch.setitem(COMMAND_UPCASTERS.current, "command", 3)
    with pytest.raises(StorageError, match="Missing upcaster"):
        await play.store.history(cid)


def test_retired_transcript_upcasts_to_receipt_without_losing_exact_input() -> None:
    original: dict[str, object] = {
        "event": {
            "input": '{"kind":"wait"}',
            "action": "typed-action",
            "outcome": '{"status":"committed"}',
            "roll": None,
        },
        "command_input": None,
    }
    migrated = COMMAND_UPCASTERS.read("command", 1, original)
    assert migrated["command_input"] == '{"kind":"wait"}'
    assert migrated["event"] == {"action": "typed-action", "outcome": '{"status":"committed"}'}
    assert "input" in validation.mapping(
        original["event"]
    )  # Registry migrations do not mutate retained bytes.
