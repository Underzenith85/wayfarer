"""Authenticated campaign projection and resumable stream contracts."""

from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from test_actions import actor_setup, campaign, engine, resource_seed, world

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import Wait
from wayfarer.transport.campaign_api import create_campaign_app

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[str, str]]:
    reducer = engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "api.sqlite", 10), reducer)
    initial = campaign(reducer)
    await play.create(
        initial,
        world(),
        resource_seed(),
        (actor_setup(),),
        (
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="spectator"),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    app = create_campaign_app(
        CampaignAccess(play), {"alice-secret": "alice", "bob-secret": "bob", "gm-secret": "gm"}
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}", initial["id"]
    finally:
        await runner.cleanup()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_auth_control_projection_and_cross_campaign_privacy(api: tuple[str, str]) -> None:
    base, cid = api
    async with aiohttp.ClientSession() as client:
        async with client.get(f"{base}/campaigns/{cid}") as response:
            assert response.status == 401
        async with client.get(f"{base}/campaigns/{cid}", headers=auth("wrong")) as response:
            assert response.status == 401
        async with client.get(f"{base}/campaigns/{cid}", headers=auth("alice-secret")) as response:
            data = await response.json()
            assert response.status == 200 and data["actors"] == ["a"]
            assert "world" not in data and "perspectives" in data
        async with client.get(f"{base}/campaigns/{cid}", headers=auth("gm-secret")) as response:
            assert "world" in await response.json()
        async with client.get(
            f"{base}/campaigns/missing", headers=auth("alice-secret")
        ) as response:
            assert response.status == 404
        async with client.post(
            f"{base}/campaigns/{cid}/commands",
            headers=auth("bob-secret"),
            json=Wait(id="forged", actor_id="a", expected_revision=0, ticks=1).model_dump(),
        ) as response:
            assert response.status == 403


async def test_command_retry_and_stream_reconnect_use_revision_cursor(api: tuple[str, str]) -> None:
    base, cid = api
    command = Wait(id="wait", actor_id="a", expected_revision=0, ticks=2).model_dump(mode="json")
    async with aiohttp.ClientSession() as client:
        for _ in range(2):
            async with client.post(
                f"{base}/campaigns/{cid}/commands", headers=auth("alice-secret"), json=command
            ) as response:
                assert response.status == 200 and (await response.json())["revision"] == 1
        async with client.get(
            f"{base}/campaigns/{cid}/events?after=0", headers=auth("alice-secret")
        ) as response:
            events = (await response.json())["events"]
            assert len(events) == 1 and events[0]["cursor"] == 1
        async with client.get(
            f"{base}/campaigns/{cid}/events?after=1", headers=auth("alice-secret")
        ) as response:
            assert (await response.json())["events"] == []
        async with client.get(f"{base}/health") as response:
            assert response.status == 200
