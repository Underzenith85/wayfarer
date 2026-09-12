import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from wayfarer.engine.character import builder
from wayfarer.engine.simulation.campaign.scenario import scenario
from wayfarer.orchestration.service import GameService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_secret_filtering(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    assert "secret" not in created["scenario"] and "clue" not in created["scenario"]


async def test_event_loop_remains_responsive_during_database_io(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    marker = asyncio.Event()

    async def concurrent() -> None:
        await asyncio.sleep(0)
        marker.set()

    await asyncio.gather(service.read(created["id"]), concurrent())
    assert marker.is_set()


async def test_legacy_database_remains_readable(tmp_path: Path, service: GameService) -> None:
    path = tmp_path / "legacy.sqlite3"
    state = {
        "id": "legacy",
        "revision": 1,
        "rules": "wayfarer-lite-1",
        "character": builder.character(),
        "scenario": scenario(),
        "hp": 10,
        "fp": 11,
        "minutes": 30,
        "location": "Blackwater docks",
        "inventory": ["Travel clothes"],
        "discoveries": [],
        "flags": [],
        "complete": False,
        "messages": [{"role": "gm", "text": "You rest."}],
    }
    db = sqlite3.connect(path)
    with db:
        db.execute("CREATE TABLE campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))"
        )
        db.execute("INSERT INTO campaigns VALUES (?,?)", ("legacy", json.dumps(state)))
        db.execute(
            "INSERT INTO events VALUES (?,?,?)", ("legacy", "old", json.dumps({"input": "Rest"}))
        )
    db.close()
    assert isinstance(service.store, AsyncSQLiteStore)
    service.store.path = path
    assert (await service.read("legacy"))["minutes"] == 30
    assert (await service.store.duplicate("legacy", "old", "Rest")) is not None
    assert not hasattr(service, "turn") and not hasattr(service, "interpret")
