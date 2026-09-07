"""Review-first provider proposals never overwrite or activate scenario state."""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer
from test_runtime import settings

from wayfarer.orchestration.providers import Orchestrator, ProviderReply, ProviderRequest, Usage
from wayfarer.runtime import create_runtime_app
from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY
from wayfarer.transport.setup_api import SETUP_KEY

PREFIX = "/authoring/v1/scenarios"
HEADERS = {"Authorization": "Bearer alice-token"}


class PublicProposalProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: ProviderRequest) -> object:
        self.calls += 1
        context = json.loads(request.context_json)
        public = context["current"]
        public["title"] = "The Revised Beacon"
        public["summary"] = "A tense investigation with a rescue route."
        return ProviderReply(payload_json=json.dumps(public), usage=Usage())


class BlockingPublicProvider(PublicProposalProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    async def complete(self, request: ProviderRequest) -> object:
        self.calls += 1
        await self.release.wait()
        context = json.loads(request.context_json)
        return ProviderReply(payload_json=json.dumps(context["current"]), usage=Usage())


async def create_scenario(client: TestClient) -> dict[str, object]:
    template_response = await client.get(PREFIX + "/templates", headers=HEADERS)
    assert template_response.status == 200
    template = (await template_response.json())[0]
    response = await client.post(
        PREFIX,
        headers=HEADERS,
        json={
            "id": str(uuid4()),
            "operation": "create",
            "expected_version": 0,
            "content_json": json.dumps(template),
        },
    )
    assert response.status == 200, await response.text()
    return await response.json()


async def wait_for_job(client: TestClient, cid: str, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = await client.get(
            f"{PREFIX}/{cid}/generation-jobs/{job_id}", headers=HEADERS
        )
        assert response.status == 200, await response.text()
        job = await response.json()
        if job["status"] not in ("queued", "running"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("generation job did not settle")


async def test_assisted_proposal_requires_explicit_save(tmp_path: Path) -> None:
    config = settings(tmp_path)
    app = create_runtime_app(config, config.frontend_dir)
    provider = PublicProposalProvider()
    app[ORCHESTRATOR_KEY] = Orchestrator(
        app[SETUP_KEY].access, provider, timeout=2, attempts=1
    )
    async with TestClient(TestServer(app)) as client:
        entry = await create_scenario(client)
        cid = str(entry["id"])
        response = await client.post(
            f"{PREFIX}/{cid}/generate",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "expected_version": 1,
                "revision": 1,
                "section": "public",
                "instruction": "Make it more investigative and add a rescue route.",
            },
        )
        assert response.status == 202, await response.text()
        started = await response.json()
        job = await wait_for_job(client, cid, started["id"])
        assert job["status"] == "needs_review"
        assert job["report"]["status"] == "playable"
        assert provider.calls == 1

        # Provider output is durable review material only; the saved revision is untouched.
        response = await client.get(f"{PREFIX}/{cid}", headers=HEADERS)
        view = await response.json()
        assert view["entry"]["version"] == 1
        assert view["entry"]["revision"] == 1
        assert view["revision"]["draft"]["content_json"] != job["proposal_json"]
        assert view["generation_jobs"][-1]["id"] == job["id"]

        response = await client.post(
            f"{PREFIX}/{cid}",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "operation": "save",
                "expected_version": 1,
                "revision": 1,
                "content_json": job["proposal_json"],
            },
        )
        assert response.status == 200, await response.text()
        saved = await response.json()
        assert saved["version"] == 2 and saved["revision"] == 2
        response = await client.get(f"{PREFIX}/{cid}/export?revision=2", headers=HEADERS)
        assert json.loads(await response.text())["public"]["title"] == "The Revised Beacon"


async def test_cancelled_or_late_generation_cannot_overwrite_human_edits(tmp_path: Path) -> None:
    config = settings(tmp_path)
    app = create_runtime_app(config, config.frontend_dir)
    provider = BlockingPublicProvider()
    app[ORCHESTRATOR_KEY] = Orchestrator(
        app[SETUP_KEY].access, provider, timeout=2, attempts=1
    )
    async with TestClient(TestServer(app)) as client:
        entry = await create_scenario(client)
        cid = str(entry["id"])
        response = await client.get(f"{PREFIX}/{cid}", headers=HEADERS)
        original = await response.json()
        source = json.loads(original["revision"]["draft"]["content_json"])
        source["public"]["summary"] = "Human edit wins."

        response = await client.post(
            f"{PREFIX}/{cid}/generate",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "expected_version": 1,
                "revision": 1,
                "section": "public",
                "instruction": "Rewrite the public brief.",
            },
        )
        started = await response.json()
        for _ in range(100):
            state = await client.get(
                f"{PREFIX}/{cid}/generation-jobs/{started['id']}", headers=HEADERS
            )
            if (await state.json())["status"] == "running":
                break
            await asyncio.sleep(0.01)

        # Saving while the provider is running does not conflict: job state is independent.
        response = await client.post(
            f"{PREFIX}/{cid}",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "operation": "save",
                "expected_version": 1,
                "revision": 1,
                "content_json": json.dumps(source),
            },
        )
        assert response.status == 200, await response.text()
        cancel = await client.post(
            f"{PREFIX}/{cid}/generation-jobs/{started['id']}/cancel",
            headers=HEADERS,
            json={"id": str(uuid4())},
        )
        assert cancel.status == 200
        assert (await cancel.json())["status"] == "cancelled"
        provider.release.set()
        await asyncio.sleep(0)
        response = await client.get(f"{PREFIX}/{cid}/export?revision=2", headers=HEADERS)
        assert json.loads(await response.text())["public"]["summary"] == "Human edit wins."

        # Job payloads remain owner-only and recoverable after a normal catalog read.
        response = await client.get(
            f"{PREFIX}/{cid}/generation-jobs/{started['id']}",
            headers={"Authorization": "Bearer bob-token"},
        )
        assert response.status == 404
        response = await client.get(f"{PREFIX}/{cid}", headers=HEADERS)
        jobs = (await response.json())["generation_jobs"]
        assert jobs[-1]["status"] == "cancelled"


async def test_provider_unavailable_keeps_manual_authoring(tmp_path: Path) -> None:
    config = settings(tmp_path)
    async with TestClient(TestServer(create_runtime_app(config, config.frontend_dir))) as client:
        entry = await create_scenario(client)
        response = await client.post(
            f"{PREFIX}/{entry['id']}/generate",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "expected_version": 1,
                "revision": 1,
                "instruction": "Make this darker.",
            },
        )
        assert response.status == 503
        assert "manual/template authoring" in (await response.json())["error"]
        response = await client.get(f"{PREFIX}/{entry['id']}", headers=HEADERS)
        assert response.status == 200
