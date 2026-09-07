"""Real HTTP services with independent, explicitly test-only Basic Set bindings."""

import tempfile
from pathlib import Path

from aiohttp import web
from test_tactical import setup

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import RecordedDice
from wayfarer.transport.campaign_api import create_campaign_app


class TestAccess(CampaignAccess):
    def __init__(self, play: PlayService) -> None:
        super().__init__(play)
        self.campaigns: dict[str, CampaignAccess] = {}

    async def runtime(self, cid: str) -> CampaignAccess:
        return self.campaigns.get(cid) or await super().runtime(cid)


async def application() -> web.Application:
    _, play = await setup(Path(tempfile.mkdtemp(prefix="tactical-base-")))
    access = TestAccess(play)
    app = create_campaign_app(access, {f"{p}-token": p for p in ("alice", "bob", "charlie", "gm")})

    async def fixture(request: web.Request) -> web.Response:
        cid, runtime = await setup(
            Path(tempfile.mkdtemp(prefix="tactical-browser-")),
            unarmed=request.query.get("unarmed") == "true",
        )
        runtime.rng = RecordedDice((3, 3, 3) * 1000)
        access.campaigns[cid] = CampaignAccess(runtime)
        return web.json_response({"campaign_id": cid})

    app.router.add_post("/test-tactical", fixture)
    return app


web.run_app(application(), host="127.0.0.1", port=8015, print=None)
