"""Atomic stream append, independent folding, explicit audiences and old log migration."""

import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from support.runtime import build_runtime
from test_wave9 import prepare

from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.events import (
    EVENT_ADAPTER,
    ActorAudience,
    CommandApplied,
    StatePatched,
    document,
    visible,
)
from wayfarer.errors import StorageError, ValidationError
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import fold


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_atomic_stream_fold_retry_and_schema(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend=backend)
    initial = await play.store.read(cid)
    access = build_runtime(play)
    command = Wait(id="stream", actor_id="a", expected_revision=0, ticks=1)
    await access.submit_json(cid, command.model_dump(mode="json"), principal_id="alice")
    stream = await play.store.stream(cid)
    assert stream and {e.command_id for e in stream} == {"stream"}
    assert [e.ordinal for e in stream] == list(range(len(stream)))
    assert all(e.schema_version == 1 for e in stream)
    state = fold(initial, [e.event for e in stream])
    assert document(state) == document(await play.store.read(cid))
    assert document((await play.store.stream_states(cid))[-1][0]) == document(state)
    schema = json.loads(
        (Path(__file__).parents[1] / "contracts/v1/engine-events.schema.json").read_text()
    )
    validator = Draft202012Validator(schema)
    for event in stream:
        validator.validate(json.loads(EVENT_ADAPTER.dump_json(event.event)))
    await access.submit_json(cid, command.model_dump(mode="json"), principal_id="alice")
    assert await play.store.stream(cid) == stream
    # Every retained command checkpoint is reconstructed from event operations.
    await access.submit_json(
        cid,
        Wait(id="second", actor_id="a", expected_revision=1, ticks=1).model_dump(mode="json"),
        principal_id="alice",
    )
    states = await play.store.stream_states(cid)
    history = await play.store.history(cid)
    assert [document(s) for s, _ in states[1:]] == [
        document(row.state_after) for row in history[1:]
    ]


async def test_stream_append_failure_rolls_back_everything(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    assert isinstance(play.store, AsyncSQLiteStore)
    await play.store.stream(cid)
    before = await play.store.read(cid)
    with sqlite3.connect(play.store.path) as db:
        db.execute(
            "CREATE TRIGGER reject_stream BEFORE INSERT ON event_stream BEGIN SELECT RAISE(ABORT, 'injected append failure'); END"
        )
    with pytest.raises(StorageError):
        await play.execute(
            cid,
            Wait(id="fail", actor_id="a", expected_revision=0, ticks=1),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before
    assert [r.command_id for r in await play.store.history(cid)] == ["setup:seed"]
    assert await play.store.stream(cid) == []


async def test_capture_and_rescuer_events_have_separate_audiences(tmp_path: Path) -> None:
    from test_wave10 import prepare as capture_prepare
    from test_wave10 import setback, wait

    cid, play = await capture_prepare(tmp_path)
    await setback(cid, play, "capture")
    before = play._load(await play.store.read(cid))
    await wait(cid, play, "b")
    events = await play.store.stream(cid, after=before.revision)
    alice = next(m for m in before.members if m.principal_id == "alice")
    bob = next(m for m in before.members if m.principal_id == "bob")
    gm = next(m for m in before.members if m.principal_id == "gm")
    applied = [e.event for e in events if isinstance(e.event, CommandApplied)]
    assert applied and all(e.actor_id == "b" for e in applied)
    assert all(not visible(e, alice) and visible(e, bob) and visible(e, gm) for e in applied)
    private = [e.event for e in events if isinstance(e.event, StatePatched)]
    assert private and all(not visible(e, alice) and not visible(e, bob) for e in private)
    assert all(visible(e.event, gm) for e in await play.store.stream(cid))
    assert all(
        not isinstance(e.event.audience, ActorAudience) or e.event.audience.actor_ids
        for e in events
    )


async def test_corrupt_or_reordered_stream_is_rejected(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    initial = await play.store.read(cid)
    for revision in range(2):
        await play.execute(
            cid,
            Wait(id=f"w{revision}", actor_id="a", expected_revision=revision, ticks=1),
            authenticated_actor_id="a",
        )
    events = [e.event for e in await play.store.stream(cid)]
    with pytest.raises(ValidationError, match="digest"):
        fold(initial, list(reversed(events)))
    patch = next(e for e in events if isinstance(e, StatePatched))
    with pytest.raises(ValidationError, match="digest"):
        fold(initial, [patch.model_copy(update={"after_digest": "tampered"})])


async def test_action_engine_event_list_folds_without_a_persistence_callback(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.events import fold_play

    cid, play = await prepare(tmp_path)
    before = play._load(await play.store.read(cid))
    after, events = play.engine.resolve(
        before, Wait(id="pure", actor_id="a", expected_revision=0, ticks=1)
    )
    assert isinstance(events, list) and events
    assert fold_play(before, events) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_receipts_never_write_transcript_fields(tmp_path: Path, backend: str) -> None:

    cid, play = await prepare(tmp_path, backend=backend)
    await play.execute(
        cid,
        Wait(id="receipt", actor_id="a", expected_revision=0, ticks=1),
        authenticated_actor_id="a",
    )
    from wayfarer.persistence.catalog import CatalogStore

    async with CatalogStore(play.store).transaction() as db:
        rows = await db.query(
            "SELECT event, schema_version, command_input FROM command_log WHERE campaign=? "
            "ORDER BY resulting_revision DESC",
            (cid,),
        )
    payload = rows[0][0]
    saved = json.loads(payload) if isinstance(payload, str) else payload
    assert isinstance(saved, dict) and set(saved) == {"action", "outcome"}
    assert rows[0][1] == 2 and rows[0][2]
    record = (await play.store.history(cid))[-1]
    assert record.schema_version == 2 and record.command_input == rows[0][2]
    assert set(record.event) == {"action", "outcome"}
