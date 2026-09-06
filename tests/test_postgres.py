"""PostgreSQL contract tests run in CI against the reserved service."""

import asyncio
import os
from collections.abc import AsyncIterator

import aiohttp
import pytest
import pytest_asyncio
from pydantic import SecretStr

from wayfarer.character import builder
from wayfarer.config import Settings
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.llm import LLMClient
from wayfarer.orchestration.service import GameService
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.simulation.scenario import scenario

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def postgres_service() -> AsyncIterator[GameService]:
    url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
    settings = Settings(database_url=SecretStr(url))
    async with aiohttp.ClientSession() as session:
        yield GameService(settings, LLMClient(settings, session))


async def test_postgres_concurrent_retry_and_replay(postgres_service: GameService) -> None:
    created = await postgres_service.create(builder.character(), scenario())
    results = await asyncio.gather(
        *(postgres_service.turn(created["id"], "same", 0, "Rest") for _ in range(6))
    )
    assert {result["revision"] for result in results} == {1}
    assert isinstance(postgres_service.store, AsyncPostgresStore)
    assert await postgres_service.store.replay(created["id"]) == await postgres_service.read(
        created["id"]
    )
    assert len(await postgres_service.store.history(created["id"])) == 1


async def test_postgres_rolls_back_projection_and_event_together(
    postgres_service: GameService,
) -> None:
    created = await postgres_service.create(builder.character(), scenario())
    assert isinstance(postgres_service.store, AsyncPostgresStore)

    def crash(state: Campaign) -> Event:
        state["revision"] = 99
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await postgres_service.store.commit_turn(created["id"], "crash", 0, "Rest", crash)
    assert (await postgres_service.read(created["id"]))["revision"] == 0
    assert await postgres_service.store.history(created["id"]) == []
