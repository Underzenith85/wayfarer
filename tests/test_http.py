from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from wayfarer.config import Settings
from wayfarer.transport.http import create_app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def server(tmp_path: Path) -> AsyncIterator[str]:
    app = create_app(Settings(db=tmp_path / "http.sqlite3"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    addresses = runner.addresses
    assert addresses
    base = f"http://127.0.0.1:{addresses[0][1]}"
    try:
        yield base
    finally:
        await runner.cleanup()


async def test_http_flow_and_correlation_id(server: str) -> None:
    async with aiohttp.ClientSession() as client:
        async with client.get(
            server + "/api/bootstrap", headers={"X-Request-ID": "test-correlation"}
        ) as response:
            bootstrap = await response.json()
            assert response.headers["X-Request-ID"] == "test-correlation"
        async with client.post(
            server + "/api/campaigns",
            json={"character": bootstrap["character"], "scenario": bootstrap["scenario"]},
        ) as response:
            assert response.status == 201
            campaign = await response.json()
        async with client.post(
            server + f"/api/campaigns/{campaign['id']}/turn",
            json={"request_id": "one", "revision": 0, "text": "Rest"},
        ) as response:
            result = await response.json()
            assert response.status == 410 and result["code"] == "prototype_retired"
        async with client.get(server + f"/api/campaigns/{campaign['id']}") as response:
            assert (await response.json())["revision"] == 0


async def test_http_error_mapping_and_body_limit(server: str) -> None:
    async with aiohttp.ClientSession() as client:
        async with client.post(
            server + "/api/campaigns", data="bad", headers={"Content-Type": "text/plain"}
        ) as response:
            assert response.status == 400 and (await response.json())["code"] == "validation_error"
        async with client.post(
            server + "/api/campaigns",
            data=b"x" * 33_000,
            headers={"Content-Type": "application/json"},
        ) as response:
            assert response.status in {400, 413}
