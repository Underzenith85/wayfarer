"""Deterministic live engine fixture; never used by production entrypoints."""

import tempfile
from pathlib import Path

from aiohttp import web
from test_scenes import configured
from test_wave9 import FakeProvider
from test_wave10 import prepare, setback
from test_wave12 import two_player_graph

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.orchestration.setup import SetupService
from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY, create_campaign_app, interpret
from wayfarer.transport.setup_api import SETUP_KEY
from wayfarer.transport.v1.http import SERVICE
from wayfarer.transport.v1.provider import bind_provider


async def application() -> web.Application:
    directory = Path(tempfile.mkdtemp(prefix="wave11-browser-"))
    _, play = await prepare(directory)
    access = CampaignAccess(play)
    opening = two_player_graph()
    sequel = opening.model_copy(
        update={
            "id": "sequel",
            "title": "Courier aftermath",
            "objectives": opening.objectives.model_copy(
                update={"id": "sequel-objectives", "deadline": 300}
            ),
        }
    )
    app = create_campaign_app(
        access,
        {"alice-token": "alice", "bob-token": "bob", "gm-token": "gm"},
        scenario_templates=(opening, sequel),
        legacy_routes=True,
        v1_origins=frozenset({"http://127.0.0.1:4174"}),
    )
    app[SETUP_KEY] = SetupService(CampaignAccess(PlayService(play.store, configured()[0])))
    app[ORCHESTRATOR_KEY] = Orchestrator(access, FakeProvider())
    bind_provider(app[SERVICE], app[ORCHESTRATOR_KEY])
    app.router.add_post("/campaigns/{cid}/interpret", interpret)

    async def new_fixture(_: web.Request) -> web.Response:
        cid, runtime = await prepare(directory)
        await setback(cid, runtime, "capture")
        return web.json_response({"campaign_id": cid})

    app.router.add_post("/test-campaign", new_fixture)
    return app


web.run_app(application(), host="127.0.0.1", port=8000, print=None)
