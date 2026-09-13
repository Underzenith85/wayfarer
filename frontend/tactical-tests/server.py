"""Real HTTP services with independent, explicitly test-only Basic Set bindings."""

import tempfile
from pathlib import Path

from aiohttp import web
from test_reinforcements import setup_profiled_basic
from test_tactical import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.runtime import CampaignRuntime
from wayfarer.transport.campaign_api import create_campaign_app


class TestAccess(CampaignRuntime):
    def __init__(self, play: PlayService) -> None:
        super().__init__(play)
        self.campaigns: dict[str, CampaignRuntime] = {}

    async def for_campaign(self, cid: str) -> CampaignRuntime:
        return self.campaigns.get(cid) or await super().for_campaign(cid)


async def application() -> web.Application:
    _, play = await setup(Path(tempfile.mkdtemp(prefix="tactical-base-")))
    access = TestAccess(play)
    app = create_campaign_app(
        access,
        {f"{p}-token": p for p in ("alice", "bob", "charlie", "a", "b", "c", "gm")},
    )

    async def fixture(request: web.Request) -> web.Response:
        directory = Path(tempfile.mkdtemp(prefix="tactical-browser-"))
        cid, runtime = (
            await setup_profiled_basic(directory, 1)
            if request.query.get("basic") == "true"
            else await setup(
                directory,
                unarmed=request.query.get("unarmed") == "true",
            )
        )
        runtime.rng = RecordedDice((3, 3, 3) * 1000)
        access.campaigns[cid] = CampaignRuntime(runtime)
        return web.json_response({"campaign_id": cid})

    app.router.add_post("/test-tactical", fixture)
    return app


web.run_app(application(), host="127.0.0.1", port=8015, print=None)
