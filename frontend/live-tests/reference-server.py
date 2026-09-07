"""Browser acceptance composition; production adventure with deterministic provider."""

import tempfile
from pathlib import Path

from aiohttp import web
from reference_provider import ReferenceProvider

from wayfarer.adventures.runtime import application
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.transport.campaign_api import ACCESS_KEY, ORCHESTRATOR_KEY, interpret
from wayfarer.transport.setup_api import generate
from wayfarer.transport.v1.http import SERVICE
from wayfarer.transport.v1.provider import bind_provider


class Dice:
    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        return 0


app = application(
    Path(tempfile.mkdtemp(prefix="lantern-browser-")) / "campaign.sqlite",
    {"alice-token": "alice", "bob-token": "bob", "gm-token": "gm"},
    origins=frozenset({"http://127.0.0.1:4174"}),
)
app[ACCESS_KEY].play.rng = Dice()
app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], ReferenceProvider())
bind_provider(app[SERVICE], app[ORCHESTRATOR_KEY])
app.router.add_post("/campaigns/{cid}/interpret", interpret)
app.router.add_post("/setups/{cid}/generate", generate)
web.run_app(app, host="127.0.0.1", port=8000, print=None)
