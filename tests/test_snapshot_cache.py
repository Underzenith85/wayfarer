"""Cache loss cannot change authoritative state, retries, or narration."""

import asyncio
import json
from pathlib import Path

import pytest
from test_wave9 import prepare

from wayfarer.errors import ConflictError
from wayfarer.orchestration.service import GameService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.persistence.snapshots import decode, encode
from wayfarer.simulation.actions import Wait
from wayfarer.simulation.events import document


async def sql(
    store: AsyncSQLiteStore | AsyncPostgresStore, statement: str, values: tuple[object, ...] = ()
) -> None:
    db = await store._connect()
    try:
        await db.execute(
            statement.replace("?", "%s") if isinstance(store, AsyncPostgresStore) else statement,
            values,
        )
        await db.commit()
    finally:
        await db.close()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("interval", [0, 1, 10])
async def test_cache_loss_restart_retry_and_concurrent_writers(
    tmp_path: Path, backend: str, interval: int
) -> None:
    cid, play = await prepare(tmp_path, backend=backend)
    store = play.store
    store.snapshot_interval = interval
    await sql(store, "DELETE FROM snapshots WHERE campaign=?", (cid,))
    for revision in range(11):
        await play.execute(
            cid,
            Wait(id=f"step-{revision}", actor_id="a", expected_revision=revision, ticks=1),
            authenticated_actor_id="a",
        )
    expected = await store.read(cid)
    db = await store._connect()
    try:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM snapshots WHERE campaign="
            + ("%s" if backend == "postgres" else "?"),
            (cid,),
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == (0 if interval == 0 else 11 // interval)
    finally:
        await db.close()
    from wayfarer.simulation.actions import PlayState

    assert not {"last_result", "last_combat_result", "scene_events"} & PlayState.model_fields.keys()
    encoded = json.loads(encode(expected))
    assert (
        not {"last_result", "last_combat_result", "scene_events"}
        & json.loads(encoded["campaign"]["play_json"]).keys()
    )
    assert "last_result" in encoded["event_projections"]
    assert document(decode(encode(expected))) == document(expected)
    await sql(store, "DELETE FROM snapshots WHERE campaign=?", (cid,))
    await sql(store, "UPDATE campaigns SET state='{}' WHERE id=?", (cid,))
    await sql(store, "UPDATE command_log SET state_after='{}' WHERE campaign=?", (cid,))
    reopened = (
        AsyncSQLiteStore(store.path, snapshot_interval=interval)
        if isinstance(store, AsyncSQLiteStore)
        else AsyncPostgresStore(store.database_url, snapshot_interval=interval)
    )
    play.store = reopened
    assert document(await reopened.read(cid)) == document(expected)
    assert document(await reopened.replay(cid)) == document(expected)
    assert document((await reopened.history(cid))[-1].state_after) == document(expected)
    # Lost response retries read the original revision from the stream.
    duplicate = Wait(id="step-10", actor_id="a", expected_revision=10, ticks=1)
    await asyncio.gather(
        *(play.execute(cid, duplicate, authenticated_actor_id="a") for _ in range(2))
    )
    assert document(await reopened.read(cid)) == document(expected)
    # Concurrent distinct writers still compare-and-set against the folded revision.
    outcomes = await asyncio.gather(
        *(
            play.execute(
                cid,
                Wait(id=name, actor_id="a", expected_revision=11, ticks=1),
                authenticated_actor_id="a",
            )
            for name in ("left", "right")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in outcomes) == 1
    final = await reopened.read(cid)
    assert final["revision"] == 12
    # A syntactically valid but incorrect snapshot is ignored as well.
    corrupt = final.copy()
    corrupt["hp"] += 999
    await sql(reopened, "DELETE FROM snapshots WHERE campaign=?", (cid,))
    await sql(
        reopened,
        "INSERT INTO snapshots (campaign, revision, state) VALUES (?, ?, ?)",
        (cid, 12, encode(corrupt)),
    )
    assert document(await reopened.read(cid)) == document(final)
    usage = [row for row in await reopened.schema_usage() if row["campaign"] == cid]
    assert usage and all(row["snapshot_revision"] is None for row in usage)


async def test_narration_survives_cache_rebuild_without_becoming_state(
    service: GameService,
) -> None:
    from wayfarer.character import builder
    from wayfarer.simulation.scenario import scenario

    # Use the legacy public service as well: its flavor used to mutate campaign rows.
    game = service
    created = await game.create(builder.character(), scenario())
    cid = str(created["id"])
    await game.turn(cid, "one", 0, "Rest")
    state = await game.store.read(cid)
    await game.store.save_narration(cid, state["revision"], "The lantern burns blue.")
    await sql(game.store, "DELETE FROM snapshots WHERE campaign=?", (cid,))
    await sql(game.store, "UPDATE campaigns SET state='{}' WHERE id=?", (cid,))
    assert (await game.store.read(cid))["messages"][-1]["flavor"] == "The lantern burns blue."
    assert "The lantern burns blue." not in json.dumps(await game.store.replay(cid))


async def test_checkpoint_skips_covered_schemas_and_cache_failure_requires_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.errors import StorageError
    from wayfarer.persistence.upcasters import EVENT_UPCASTERS

    cid, play = await prepare(tmp_path)
    play.store.snapshot_interval = 1
    await play.execute(
        cid,
        Wait(id="covered", actor_id="a", expected_revision=0, ticks=1),
        authenticated_actor_id="a",
    )
    expected = await play.store.read(cid)
    monkeypatch.setitem(EVENT_UPCASTERS.current, "state.patched", 2)
    assert await play.store.read(cid) == expected
    await sql(play.store, "DELETE FROM snapshots WHERE campaign=?", (cid,))
    with pytest.raises(StorageError, match="Missing upcaster"):
        await play.store.read(cid)


async def test_legacy_narration_is_imported_before_replacing_cache(service: GameService) -> None:
    from wayfarer.character import builder
    from wayfarer.simulation.scenario import scenario

    created = await service.create(builder.character(), scenario())
    cid = created["id"]
    await service.turn(cid, "old", 0, "Rest")
    cached = await service.store.read(cid)
    cached["messages"][-1]["flavor"] = "Previously displayed narration"
    await sql(service.store, "UPDATE campaigns SET state=? WHERE id=?", (json.dumps(cached), cid))
    await sql(service.store, "DELETE FROM narration_migrations WHERE campaign=?", (cid,))
    assert (await service.store.read(cid))["messages"][-1][
        "flavor"
    ] == "Previously displayed narration"
    await sql(service.store, "UPDATE campaigns SET state='{}' WHERE id=?", (cid,))
    assert (await service.store.read(cid))["messages"][-1][
        "flavor"
    ] == "Previously displayed narration"
