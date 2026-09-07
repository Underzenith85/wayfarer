"""Deterministic live engine fixture; never used by production entrypoints."""

import tempfile
from pathlib import Path

from aiohttp import web
from test_wave9 import FakeProvider
from test_wave10 import prepare, setback

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY, create_campaign_app, interpret


async def application() -> web.Application:
    directory = Path(tempfile.mkdtemp(prefix="wave11-browser-"))
    _, play = await prepare(directory)
    access = CampaignAccess(play)
    app = create_campaign_app(
        access, {"alice-token": "alice", "bob-token": "bob", "gm-token": "gm"}, legacy_routes=True
    )
    app[ORCHESTRATOR_KEY] = Orchestrator(access, FakeProvider())
    app.router.add_post("/campaigns/{cid}/interpret", interpret)

    async def new_fixture(_: web.Request) -> web.Response:
        cid, runtime = await prepare(directory)
        await setback(cid, runtime, "capture")
        return web.json_response({"campaign_id": cid})

    app.router.add_post("/test-campaign", new_fixture)
    return app


web.run_app(application(), host="127.0.0.1", port=8000, print=None)
