"""Real authoring API, database restart, ownership, retries and isolated games."""

import asyncio
import json
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from pydantic import SecretStr
from test_runtime import settings

from wayfarer.config import Settings
from wayfarer.orchestration.catalog import GeneratedScenarioGraph
from wayfarer.orchestration.providers import Orchestrator, ProviderReply, ProviderRequest, Usage
from wayfarer.runtime import create_runtime_app, starting_scenario
from wayfarer.transport.campaign_api import ACCESS_KEY, ORCHESTRATOR_KEY
from wayfarer.transport.setup_api import SETUP_KEY

PREFIX = "/authoring/v1/scenarios"
HEADERS = {"Authorization": "Bearer alice-token"}


def test_generation_schema_requires_fresh_portable_state() -> None:
    graph = starting_scenario()
    GeneratedScenarioGraph.model_validate_json(graph.model_dump_json())
    stale = graph.model_copy(
        update={"resources": graph.resources.model_copy(update={"revision": 1})}
    )
    with pytest.raises(ValueError, match="Input should be 0"):
        GeneratedScenarioGraph.model_validate_json(stale.model_dump_json())


async def test_generation_publishes_safe_provider_diagnostic(tmp_path: Path) -> None:
    from wayfarer.orchestration.codex import CodexLimitError

    class Unavailable:
        async def complete(self, request: ProviderRequest) -> object:
            error = CodexLimitError("SECRET_TOKEN private model response")
            error.stage = "turn_stream"
            raise error

    config = settings(tmp_path)
    app = create_runtime_app(config, config.frontend_dir)
    app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], Unavailable(), attempts=1)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            PREFIX + "/generation-jobs",
            headers=HEADERS,
            json={"id": str(uuid4()), "brief": starting_scenario().brief.model_dump(mode="json")},
        )
        assert response.status == 202
        job = await response.json()
        async with asyncio.timeout(5):
            while job["status"] in ("queued", "running"):
                job = await (
                    await client.get(PREFIX + f"/generation-jobs/{job['id']}", headers=HEADERS)
                ).json()
                await asyncio.sleep(0)
        assert job["status"] == "failed"
        assert job["error_code"] == "codex_subscription_limit"
        assert "turn execution" in job["error_message"]
        assert "SECRET_TOKEN" not in json.dumps(job)


@pytest.fixture(params=["sqlite", "postgres"])
def config(tmp_path: Path, request: pytest.FixtureRequest) -> Settings:
    value = settings(tmp_path)
    if request.param == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if not url:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        value = value.model_copy(update={"database_url": SecretStr(url)})
    return value


async def post(
    client: TestClient[web.Request, web.Application],
    path: str,
    data: Mapping[str, object],
    status: int = 200,
) -> dict[str, object]:
    response = await client.post(PREFIX + path, headers=HEADERS, json=data)
    assert response.status == status, await response.text()
    result: dict[str, object] = await response.json()
    return result


async def test_catalog_restart_revisions_isolation(config: Settings) -> None:
    app = create_runtime_app(config, config.frontend_dir)
    create_id = str(uuid4())
    async with TestClient(TestServer(app)) as client:
        response = await client.get(PREFIX + "/templates", headers=HEADERS)
        document = (await response.json())[0]
        document["gm_notes"] = "Hidden antagonist plans"
        source = json.dumps(document)
        command = {"id": create_id, "operation": "create", "content_json": source}
        entry = await post(client, "", command)
        cid = str(entry["id"])
        assert entry["status"] == "playable"
        # Concurrent first-create retries cannot duplicate aggregates.
        duplicates = await asyncio.gather(post(client, "", command), post(client, "", command))
        assert all(duplicate == entry for duplicate in duplicates)
        await post(client, "", {**command, "content_json": "{}"}, 409)
        response = await client.get(
            f"{PREFIX}/{cid}", headers={"Authorization": "Bearer bob-token"}
        )
        assert response.status == 404
        assert (await client.get(PREFIX)).status == 401
        await post(
            client, f"/{cid}", {"id": str(uuid4()), "operation": "publish", "expected_version": 1}
        )
        response = await client.get(f"{PREFIX}/{cid}/preview", headers=HEADERS)
        preview = await response.text()
        assert "Hidden antagonist" not in preview and "gm_notes" not in preview
        response = await client.get(f"{PREFIX}/{cid}/export?revision=1", headers=HEADERS)
        exported = await response.text()
        assert json.loads(exported)["gm_notes"] == document["gm_notes"]
        imported = await post(
            client, "", {"id": str(uuid4()), "operation": "import", "content_json": exported}
        )
        assert imported["id"] != cid
        response = await client.get(f"{PREFIX}/{imported['id']}/export", headers=HEADERS)
        fork = json.loads(await response.text())
        assert {k: v for k, v in fork["graph"].items() if k != "id"} == {
            k: v for k, v in json.loads(exported)["graph"].items() if k != "id"
        }
        game_command = {"id": str(uuid4()), "revision": 1}
        game1 = await post(client, f"/{cid}/instantiate", game_command, 201)
        game2 = await post(client, f"/{cid}/instantiate", {"id": str(uuid4()), "revision": 1}, 201)
        assert game1["id"] != game2["id"]
        assert await post(client, f"/{cid}/instantiate", game_command, 201) == game1
        await post(
            client, f"/{cid}/instantiate", {"id": str(uuid4()), "revision": 1, "party": []}, 400
        )
        # Saves retain invalid drafts without changing the published revision or either game.
        draft = await post(
            client,
            f"/{cid}",
            {
                "id": str(uuid4()),
                "operation": "save",
                "expected_version": 2,
                "content_json": "{unfinished",
            },
        )
        assert draft["status"] == "invalid" and draft["revision"] == 2
        await post(
            client,
            f"/{cid}",
            {"id": str(uuid4()), "operation": "save", "expected_version": 2, "content_json": "{}"},
            409,
        )
        await post(
            client,
            f"/{cid}",
            {"id": str(uuid4()), "operation": "publish", "expected_version": 3},
            400,
        )
        await post(client, f"/{cid}/instantiate", {"id": str(uuid4()), "revision": 2}, 400)
    # Brand-new app and database connections reopen exact saved source and receipts.
    async with TestClient(TestServer(create_runtime_app(config, config.frontend_dir))) as client:
        response = await client.get(f"{PREFIX}/{cid}/export", headers=HEADERS)
        assert await response.text() == "{unfinished"
        response = await client.get(f"{PREFIX}/{cid}/export?revision=1", headers=HEADERS)
        assert await response.text() == exported
        assert await post(client, "", command) == entry
        for game in (game1, game2):
            from wayfarer.engine.simulation.scenario_references import boundary, verify

            saved = await client.app[ACCESS_KEY].play.store.read(str(game["id"]))
            pin = boundary(saved)
            assert pin is not None and pin.reference.catalog_id == cid
            assert pin.reference.revision == 1 and pin.published is not None
            assert pin.published.content_json == exported
            verify(saved)
            game_id = str(game["id"])
            steps: list[dict[str, object]] = [
                {"operation": "assign", "principal_id": "alice", "actor_ids": ["mira"]},
                {"operation": "ready"},
                {"operation": "activate"},
            ]
            for revision, fields in enumerate(steps):
                response = await client.post(
                    f"/setups/{game_id}",
                    headers=HEADERS,
                    json={
                        "id": str(uuid4()),
                        "expected_revision": revision,
                        **fields,
                    },
                )
                assert response.status == 200, await response.text()
        store = client.app[SETUP_KEY].play.store
        before2 = await store.read(str(game2["id"]))
        first = await store.read(str(game1["id"]))
        assert first["scenario_document_json"] == before2["scenario_document_json"] == exported
        response = await client.post(
            f"/setups/{game1['id']}",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "expected_revision": 3,
                "operation": "pause",
            },
        )
        assert response.status == 200
        assert await store.read(str(game2["id"])) == before2
        # Archive keeps pinned games and immutable exports recoverable.
        await post(
            client, f"/{cid}", {"id": str(uuid4()), "operation": "archive", "expected_version": 3}
        )
        response = await client.get(f"/setups/{game2['id']}", headers=HEADERS)
        assert (await response.json())["phase"] == "active"


async def test_catalog_import_security_and_authority(config: Settings) -> None:
    async with TestClient(TestServer(create_runtime_app(config, config.frontend_dir))) as client:
        response = await client.get(PREFIX + "/templates", headers=HEADERS)
        document = (await response.json())[0]
        for invalid in (
            "{}",
            "{",
            '{"schema_version":999}',
            '{"schema_version":1,"schema_version":1}',
        ):
            await post(
                client,
                "",
                {"id": str(uuid4()), "operation": "import", "content_json": invalid},
                400,
            )
        document["compatibility"]["engine_digest"] = "0" * 64
        await post(
            client,
            "",
            {"id": str(uuid4()), "operation": "import", "content_json": json.dumps(document)},
            400,
        )
        await post(
            client,
            "",
            {"id": str(uuid4()), "operation": "create", "content_json": "é" * 1_000_001},
            400,
        )
        entry = await post(
            client, "", {"id": str(uuid4()), "operation": "create", "content_json": "{}"}
        )
        for op in ("save", "publish", "archive", "duplicate"):
            response = await client.post(
                f"{PREFIX}/{entry['id']}",
                headers={"Authorization": "Bearer bob-token"},
                json={
                    "id": str(uuid4()),
                    "operation": op,
                    "expected_version": 1,
                    "revision": 1,
                    "content_json": "{}",
                },
            )
            assert response.status == 404
        for suffix in ("", "/export", "/preview"):
            assert (
                await client.get(
                    f"{PREFIX}/{entry['id']}{suffix}", headers={"Authorization": "Bearer bob-token"}
                )
            ).status == 404
        # Untrusted document metadata cannot name its owner or confer author authority.
        response = await client.post(
            PREFIX,
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "operation": "create",
                "content_json": "{}",
                "owner_id": "bob",
            },
        )
        assert response.status == 400


async def test_guided_generation_is_recoverable_and_never_overwrites_edits(
    config: Settings,
) -> None:
    graph = starting_scenario()

    class Provider:
        async def complete(self, request: ProviderRequest) -> object:
            assert request.operation == "scenario_draft"
            assert "catalog_ids" in request.context_json
            return ProviderReply(payload_json=graph.model_dump_json(), usage=Usage())

    app = create_runtime_app(config, config.frontend_dir)
    app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], Provider())
    async with TestClient(TestServer(app)) as client:
        catalog_before_generation = await (await client.get(PREFIX, headers=HEADERS)).json()
        request = {
            "id": str(uuid4()),
            "brief": graph.brief.model_dump(mode="json"),
            "instructions": "Add an investigation route",
            "party_capabilities": ["observation"],
            "section": "all",
        }
        response = await client.post(PREFIX + "/generation-jobs", headers=HEADERS, json=request)
        assert response.status == 202, await response.text()
        job = await response.json()
        for _ in range(20):
            response = await client.get(PREFIX + f"/generation-jobs/{job['id']}", headers=HEADERS)
            job = await response.json()
            if job["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0)
        assert job["status"] == "succeeded"
        assert job["proposal_json"]
        assert job["report"]["status"] == "playable"
        # The proposal is not a catalog revision until the author explicitly accepts it.
        assert await (await client.get(PREFIX, headers=HEADERS)).json() == catalog_before_generation
        saved = await post(
            client,
            "",
            {
                "id": str(uuid4()),
                "operation": "create",
                "content_json": job["proposal_json"],
            },
        )
        assert saved["revision"] == 1


async def test_guided_generation_keeps_an_earlier_portable_candidate(config: Settings) -> None:
    graph = starting_scenario()
    first = graph.model_copy(update={"opening_scene_id": "missing-scene"})
    regressed = graph.model_copy(update={"npc_actor_ids": ("missing-actor",)})

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request: ProviderRequest) -> object:
            self.calls += 1
            proposal = first if self.calls == 1 else regressed
            return ProviderReply(payload_json=proposal.model_dump_json(), usage=Usage())

    provider = Provider()
    app = create_runtime_app(config, config.frontend_dir)
    app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], provider)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            PREFIX + "/generation-jobs",
            headers=HEADERS,
            json={"id": str(uuid4()), "brief": graph.brief.model_dump(mode="json")},
        )
        assert response.status == 202, await response.text()
        job = await response.json()
        for _ in range(20):
            response = await client.get(PREFIX + f"/generation-jobs/{job['id']}", headers=HEADERS)
            job = await response.json()
            if job["status"] not in ("queued", "running"):
                break
            await asyncio.sleep(0)
        assert provider.calls == 2
        assert job["status"] == "needs_review"
        assert job["proposal_json"]
        assert job["report"]["status"] == "invalid"
        # An exhausted budget names what is still unresolved, not just the budget (#365).
        exhausted = next(
            f for f in job["report"]["findings"] if f["code"] == "generation.repair_exhausted"
        )
        assert exhausted["reference"] == "missing-scene"
        assert "4 unresolved finding(s)" in exhausted["message"]
        assert "opening.missing at missing-scene" in exhausted["message"]
        assert "retry generation with instructions that address them" in exhausted["message"]


async def test_guided_generation_cancel_and_restart_recovery(config: Settings) -> None:
    gate = asyncio.Event()

    class Provider:
        async def complete(self, request: ProviderRequest) -> object:
            await gate.wait()
            return ProviderReply(payload_json=starting_scenario().model_dump_json(), usage=Usage())

    app = create_runtime_app(config, config.frontend_dir)
    app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], Provider())
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            PREFIX + "/generation-jobs",
            headers=HEADERS,
            json={
                "id": str(uuid4()),
                "brief": starting_scenario().brief.model_dump(mode="json"),
            },
        )
        job = await response.json()
        await asyncio.sleep(0)
        response = await client.post(
            PREFIX + f"/generation-jobs/{job['id']}/cancel", headers=HEADERS, json={}
        )
        assert response.status == 200, await response.text()
        assert (await response.json())["status"] == "cancelled"
        gate.set()
        await asyncio.sleep(0)
        response = await client.get(PREFIX + f"/generation-jobs/{job['id']}", headers=HEADERS)
        assert (await response.json())["status"] == "cancelled"


def test_authoring_contract_schema_drift() -> None:
    from wayfarer.engine.simulation.catalog import (
        CatalogCommand,
        CatalogSummary,
        InstantiateRevision,
        RevisionView,
        ScenarioGenerationJob,
        ScenarioGenerationRequest,
    )
    from wayfarer.engine.simulation.scenario_document import PlayerScenarioExport, ScenarioDocument

    models = (
        CatalogCommand,
        InstantiateRevision,
        CatalogSummary,
        RevisionView,
        ScenarioGenerationRequest,
        ScenarioGenerationJob,
        ScenarioDocument,
        PlayerScenarioExport,
    )
    expected = {model.__name__: model.model_json_schema() for model in models}
    assert json.loads(Path("contracts/authoring/v1/schemas.json").read_text()) == expected
