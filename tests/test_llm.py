import asyncio
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from pydantic import SecretStr

from wayfarer.config import Settings
from wayfarer.errors import ProviderTimeoutError
from wayfarer.orchestration import llm

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
async def slow_provider(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    entered = asyncio.Event()

    async def response(_request: web.Request) -> web.Response:
        entered.set()
        await asyncio.sleep(0.2)
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/responses", response)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    addresses = runner.addresses
    assert addresses
    url = f"http://127.0.0.1:{addresses[0][1]}/responses"
    monkeypatch.setattr(llm, "RESPONSES_URL", url)
    try:
        yield url
    finally:
        await runner.cleanup()


async def test_provider_timeout_is_typed(slow_provider: str) -> None:
    settings = Settings(
        openai_api_key=SecretStr("test"), openai_model="test", model_timeout_seconds=0.01
    )
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ProviderTimeoutError):
            await llm.LLMClient(settings, session).generate("test", {}, llm.ACTION_SCHEMA)


async def test_cancellation_propagates(slow_provider: str) -> None:
    settings = Settings(
        openai_api_key=SecretStr("test"), openai_model="test", model_timeout_seconds=60
    )
    async with aiohttp.ClientSession() as session:
        task = asyncio.create_task(
            llm.LLMClient(settings, session).generate("test", {}, llm.ACTION_SCHEMA)
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
