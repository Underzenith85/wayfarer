"""The normal runtime starts clean and recovers playable setups without fixtures."""

import uuid
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wayfarer.config import Settings
from wayfarer.runtime import create_runtime_app
from wayfarer.transport.setup_api import SETUP_KEY


def settings(tmp_path: Path) -> Settings:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "assets").mkdir()
    (frontend / "index.html").write_text("<h1>Production entry</h1>")
    return Settings(
        db=tmp_path / "game.sqlite",
        frontend_dir=frontend,
        tokens={"alice-token": "alice", "bob-token": "bob"},
    )


async def test_normal_runtime_restart_and_opening_action(tmp_path: Path) -> None:
    config = settings(tmp_path)
    headers = {"Authorization": "Bearer alice-token"}
    app = create_runtime_app(config, config.frontend_dir)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/")).status == 200
        assert (await client.get("/setups")).status == 401
        response = await client.get("/setups/session", headers=headers)
        assert await response.json() == {
            "principal_id": "alice",
            "generation_available": False,
            "legacy_available": False,
        }
        response = await client.get("/setups", headers=headers)
        assert await response.json() == []
        response = await client.get("/setups/templates", headers=headers)
        graph = (await response.json())[0]
        response = await client.post(
            "/setups",
            headers=headers,
            json={"id": str(uuid.uuid4()), "brief": graph["brief"], "graph": graph},
        )
        assert response.status == 201
        cid = (await response.json())["id"]
    restarted = create_runtime_app(config, config.frontend_dir)
    async with TestClient(TestServer(restarted)) as client:
        response = await client.get("/setups", headers=headers)
        assert (await response.json())[0]["graph"] == graph
        commands: list[dict[str, object]] = [
            {"operation": "assign", "principal_id": "alice", "actor_ids": ["mira"]},
            {"operation": "ready"},
            {"operation": "activate"},
        ]
        for revision, fields in enumerate(commands):
            response = await client.post(
                f"/setups/{cid}",
                headers=headers,
                json={"id": str(uuid.uuid4()), "expected_revision": revision, **fields},
            )
            assert response.status == 200, await response.text()
        response = await client.get(f"/api/v1/campaigns/{cid}", headers=headers)
        assert response.status == 200, await response.text()
        response = await client.get(f"/api/v1/campaigns/{cid}/scenes", headers=headers)
        scene = (await response.json())["items"][0]
        response = await client.get(f"/api/v1/campaigns/{cid}/characters", headers=headers)
        character = (await response.json())["items"][0]
        response = await client.get(
            f"/api/v1/campaigns/{cid}/characters/mira/inventory", headers=headers
        )
        inventory = await response.json()
        action = {
            "command_id": str(uuid.uuid4()),
            "actor_id": "mira",
            "scene_id": scene["id"],
            "expected_versions": {
                "scene": scene["version"],
                "character": character["version"],
                "inventory": inventory["version"],
            },
            "intent": {"kind": "wait", "ticks": 1},
        }
        response = await client.post(
            f"/api/v1/campaigns/{cid}/actions", headers=headers, json=action
        )
        assert response.status == 202, await response.text()
        from wayfarer.transport.v1.http import SERVICE

        aid = (await response.json())["id"]
        await client.app[SERVICE].resolve(aid)
        response = await client.get(f"/api/v1/campaigns/{cid}/actions/{aid}", headers=headers)
        assert (await response.json())["status"] == "succeeded"
    recovered = create_runtime_app(config, config.frontend_dir)
    async with TestClient(TestServer(recovered)) as client:
        response = await client.get("/setups", headers=headers)
        assert (await response.json())[0]["phase"] == "active"
        access = await client.app[SETUP_KEY].access.runtime(cid)
        state = access.play._load(await access.play.store.read(cid))
        assert state.resources.game_time == 1
        assert state.actor_scenes[0].scene_id == "harbor-scene"
        response = await client.get(f"/setups/{cid}", headers={"Authorization": "Bearer bob-token"})
        assert response.status == 404


async def test_campaign_scoped_routes_serve_the_application_entry(tmp_path: Path) -> None:
    """A bookmarked or shared campaign URL must load the app, not a 404."""
    config = settings(tmp_path)
    app = create_runtime_app(config, config.frontend_dir)
    async with TestClient(TestServer(app)) as client:
        for path in ("/", "/character", "/journal", "/c/abc-1", "/c/abc-1/journal"):
            response = await client.get(path)
            assert response.status == 200, path
            assert "Production entry" in await response.text()
        # Anything outside those routes still needs a credential.
        for path in ("/c", "/c/abc-1/nowhere", "/campaigns/abc-1"):
            assert (await client.get(path)).status == 401, path


def test_missing_build_and_credentials_are_actionable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Frontend build missing"):
        create_runtime_app(Settings(), tmp_path)
    config = settings(tmp_path)
    with pytest.raises(ValueError, match="WAYFARER_TOKENS"):
        create_runtime_app(config.model_copy(update={"tokens": {}}), config.frontend_dir)
