"""PostgreSQL contract tests run in CI against the reserved service."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from pydantic import SecretStr

from wayfarer.config import Settings
from wayfarer.engine.character import builder
from wayfarer.engine.simulation.scenario import scenario
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.llm import LLMClient
from wayfarer.orchestration.service import GameService
from wayfarer.persistence.postgres import AsyncPostgresStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def postgres_service() -> AsyncIterator[GameService]:
    url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
    settings = Settings(database_url=SecretStr(url))
    async with aiohttp.ClientSession() as session:
        yield GameService(settings, LLMClient(settings, session))


async def test_postgres_concurrent_retry_and_replay(tmp_path: Path) -> None:
    from test_wave9 import prepare

    from wayfarer.engine.simulation.actions import Wait

    cid, play = await prepare(tmp_path, backend="postgres")
    command = Wait(id="same", actor_id="a", expected_revision=0, ticks=1)
    results = await asyncio.gather(
        *(play.execute(cid, command, authenticated_actor_id="a") for _ in range(6))
    )
    assert all(result == results[0] for result in results)
    assert await play.store.replay(cid) == await play.store.read(cid)
    assert len(await play.store.history(cid)) == 1


async def test_postgres_rolls_back_projection_and_event_together(
    postgres_service: GameService,
) -> None:
    created = await postgres_service.create(builder.character(), scenario())
    assert isinstance(postgres_service.store, AsyncPostgresStore)

    def crash(state: Campaign) -> CommandReceipt:
        state["revision"] = 99
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await postgres_service.store.commit_turn(created["id"], "crash", 0, "Rest", crash)
    assert (await postgres_service.read(created["id"]))["revision"] == 0
    assert await postgres_service.store.history(created["id"]) == []
