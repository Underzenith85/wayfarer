import asyncio
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from wayfarer.character import builder
from wayfarer.errors import ConflictError, ProviderError
from wayfarer.orchestration.service import GameService
from wayfarer.simulation.scenario import scenario

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_create_turn_retry_and_persistence(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    first = await service.turn(created["id"], "one", 0, "Rest")
    second = await service.turn(created["id"], "one", 0, "Rest")
    assert first == second
    assert (await service.read(created["id"]))["minutes"] == 30


async def test_conflicting_request_id_rejected(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    await service.turn(created["id"], "one", 0, "Rest")
    with pytest.raises(ConflictError):
        await service.turn(created["id"], "one", 1, "Inspect")


async def test_questions_do_not_spend_time(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    result = await service.turn(created["id"], "q", 0, "Could I sneak there?")
    assert result["minutes"] == 0


async def test_secret_filtering(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    assert "secret" not in created["scenario"] and "clue" not in created["scenario"]


async def test_bad_model_action_mutates_nothing(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    with (
        patch.object(type(service.llm), "enabled", new_callable=lambda: property(lambda _: True)),
        patch.object(service.llm, "generate", AsyncMock(return_value={"action": {"hp": 999}})),
    ):
        with pytest.raises(ValueError):
            await service.turn(created["id"], "bad", 0, "Look")
    assert (await service.read(created["id"]))["revision"] == 0


async def test_narration_failure_keeps_commit(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    responses: list[object] = [{"action": "rest"}, ProviderError("failed")]

    async def generate(*_args: object) -> dict[str, object]:
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, dict)
        return value

    with (
        patch.object(type(service.llm), "enabled", new_callable=lambda: property(lambda _: True)),
        patch.object(service.llm, "generate", generate),
    ):
        result = await service.turn(created["id"], "one", 0, "Rest")
    assert result["revision"] == 1 and result["minutes"] == 30


async def test_event_loop_remains_responsive_during_database_io(service: GameService) -> None:
    created = await service.create(builder.character(), scenario())
    marker = asyncio.Event()

    async def concurrent() -> None:
        await asyncio.sleep(0)
        marker.set()

    await asyncio.gather(service.read(created["id"]), concurrent())
    assert marker.is_set()


async def test_legacy_database_can_continue(tmp_path: Path, service: GameService) -> None:
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
    service.store.path = path
    assert (await service.turn("legacy", "old", 0, "Rest"))["revision"] == 1
    assert (await service.turn("legacy", "new", 1, "Rest"))["minutes"] == 60
