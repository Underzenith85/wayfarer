"""Public lifecycle contracts and atomic, private multiplayer setup."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_scenes import configured
from test_wave11 import graph_fixture

from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.setup import SetupService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.simulation.setup import CreateSetup, SetupCommand
from wayfarer.simulation.studio import ScenarioGraph
from wayfarer.transport.campaign_api import create_campaign_app


def service(tmp_path: Path) -> SetupService:
    engine, _ = configured()
    return SetupService(
        CampaignAccess(PlayService(AsyncSQLiteStore(tmp_path / "setup.sqlite"), engine))
    )


async def ready(service: SetupService) -> str:
    graph = graph_fixture()
    value = await service.create(CreateSetup(id="new", brief=graph.brief), principal_id="alice")
    cid = str(value["id"])
    for revision, command in enumerate(
        [
            SetupCommand(id="edit", expected_revision=0, operation="edit", graph=graph),
            SetupCommand(
                id="assign",
                expected_revision=1,
                operation="assign",
                principal_id="alice",
                actor_ids=("a",),
            ),
            SetupCommand(id="ready", expected_revision=2, operation="ready"),
        ]
    ):
        assert command.expected_revision == revision
        await service.execute(cid, command, principal_id="alice")
    return cid


async def test_atomic_activation_restart_and_lifecycle(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await ready(setup)
    activate = SetupCommand(id="activate", expected_revision=3, operation="activate")
    results = await asyncio.gather(
        *(setup.execute(cid, activate, principal_id="alice") for _ in range(2))
    )
    assert all(r["phase"] == "active" for r in results)
    assert (
        len([e for e in await setup.play.store.history(cid) if e.event["outcome"] == "active"]) == 1
    )
    restarted = service(tmp_path)
    access = await restarted.access.runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    assert state.actor_scenes[0].scene_id == graph_fixture().opening_scene_id
    assert state.members[0].actor_ids == ("a",)
    assert state.resources.pools
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            activate.model_copy(update={"id": "again", "expected_revision": 4}),
            principal_id="alice",
        )
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            SetupCommand(
                id="reassign",
                expected_revision=4,
                operation="assign",
                principal_id="alice",
                actor_ids=("a",),
            ),
            principal_id="alice",
        )
    await setup.execute(
        cid, SetupCommand(id="pause", expected_revision=4, operation="pause"), principal_id="alice"
    )
    with pytest.raises(ConflictError):
        await access.execute(
            cid,
            {"kind": "wait", "id": "wait", "actor_id": "a", "expected_revision": 5, "minutes": 1},
            principal_id="alice",
        )
    await setup.execute(
        cid,
        SetupCommand(id="resume", expected_revision=5, operation="resume"),
        principal_id="alice",
    )
    assert access.play._load(await access.play.store.read(cid)).lifecycle == "active"
    with pytest.raises(ValidationError):
        await setup.execute(
            cid,
            SetupCommand(id="complete", expected_revision=6, operation="complete"),
            principal_id="alice",
        )


async def test_invalid_stale_and_duplicate_setup_preserve_draft(tmp_path: Path) -> None:
    setup = service(tmp_path)
    graph = graph_fixture()
    cmd = CreateSetup(id="new", brief=graph.brief)
    values = await asyncio.gather(*(setup.create(cmd, principal_id="alice") for _ in range(2)))
    assert values[0]["id"] == values[1]["id"]
    cid = str(values[0]["id"])
    with pytest.raises(ConflictError):
        await setup.create(
            cmd.model_copy(update={"brief": graph.brief.model_copy(update={"tone": "other"})}),
            principal_id="alice",
        )
    with pytest.raises(NotFoundError):
        await setup.read(cid, principal_id="eve")
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            SetupCommand(id="activate", expected_revision=0, operation="activate"),
            principal_id="alice",
        )
    assert "play_json" not in await setup.play.store.read(cid)
    await setup.execute(
        cid,
        SetupCommand(id="edit", expected_revision=0, operation="edit", graph=graph),
        principal_id="alice",
    )
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            SetupCommand(id="stale", expected_revision=0, operation="edit"),
            principal_id="alice",
        )
    assert (await setup.read(cid, principal_id="alice"))["graph"] == graph.model_dump(mode="json")


async def test_two_credentials_invite_join_and_private_preview(tmp_path: Path) -> None:
    setup = service(tmp_path)
    app = create_campaign_app(
        setup.access, {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
    )
    async with TestClient(TestServer(app)) as client:

        async def post(
            path: str, body: dict[str, object], token: str = "alice-token"
        ) -> dict[str, object]:
            response = await client.post(
                path, json=body, headers={"Authorization": "Bearer " + token}
            )
            assert response.status in (200, 201), await response.text()
            value: dict[str, object] = await response.json()
            return value

        created = await post(
            "/setups", CreateSetup(id="new", brief=graph_fixture().brief).model_dump(mode="json")
        )
        cid = str(created["id"])
        await post(
            "/setups/" + cid,
            SetupCommand(
                id="edit", expected_revision=0, operation="edit", graph=graph_fixture()
            ).model_dump(mode="json"),
        )
        await post(
            "/setups/" + cid,
            SetupCommand(
                id="invite", expected_revision=1, operation="invite", principal_id="bob"
            ).model_dump(mode="json"),
        )
        bob = await post(
            "/setups/" + cid,
            SetupCommand(id="join", expected_revision=2, operation="join").model_dump(mode="json"),
            "bob-token",
        )
        assert bob["graph"] is None
        assert "clue" not in json.dumps(bob)
        response = await client.post(
            "/setups/" + cid,
            json=SetupCommand(
                id="steal",
                expected_revision=3,
                operation="assign",
                principal_id="bob",
                actor_ids=("a",),
            ).model_dump(mode="json"),
            headers={"Authorization": "Bearer bob-token"},
        )
        assert response.status == 403
        response = await client.get("/setups", headers={"Authorization": "Bearer bob-token"})
        assert len(await response.json()) == 1


def two_player_graph() -> ScenarioGraph:
    from test_actions import actor_setup

    from wayfarer.simulation.party import PartyRules
    from wayfarer.simulation.resources import Owner

    graph = graph_fixture()
    return graph.model_copy(
        update={
            "actors": (
                actor_setup(),
                actor_setup().model_copy(update={"actor_id": "b", "aware_of": ("a", "chest")}),
            ),
            "resources": graph.resources.model_copy(
                update={"owners": graph.resources.owners + (Owner(actor_id="b", capacity=100),)}
            ),
            "party": PartyRules(id="party", version=1),
        }
    )


async def test_two_players_activate_and_resume_through_public_api(tmp_path: Path) -> None:
    setup = service(tmp_path)
    graph = two_player_graph()
    async with TestClient(
        TestServer(
            create_campaign_app(
                setup.access,
                {"alice-token": "alice", "bob-token": "bob"},
                scenario_templates=(graph,),
                legacy_routes=True,
            )
        )
    ) as client:

        async def send(
            body: dict[str, object], principal: str = "alice", path: str = "/setups"
        ) -> dict[str, object]:
            response = await client.post(
                path, json=body, headers={"Authorization": f"Bearer {principal}-token"}
            )
            assert response.status in (200, 201), await response.text()
            result: dict[str, object] = await response.json()
            return result

        value = await send(CreateSetup(id="two", brief=graph.brief).model_dump(mode="json"))
        cid = str(value["id"])
        commands: list[tuple[str, dict[str, object]]] = [
            ("alice", {"operation": "edit", "graph": graph.model_dump(mode="json")}),
            ("alice", {"operation": "invite", "principal_id": "bob"}),
            ("bob", {"operation": "join"}),
            ("alice", {"operation": "assign", "principal_id": "alice", "actor_ids": ["a"]}),
            ("alice", {"operation": "assign", "principal_id": "bob", "actor_ids": ["b"]}),
            ("alice", {"operation": "ready"}),
            ("bob", {"operation": "ready"}),
            ("alice", {"operation": "activate"}),
        ]
        for revision, (principal, fields) in enumerate(commands):
            value = await send(
                {"id": f"command-{revision}", "expected_revision": revision, **fields},
                principal,
                "/setups/" + cid,
            )
        assert value["phase"] == "active"
    restarted = service(tmp_path)
    access = await restarted.access.runtime(cid)
    state = access.play._load(await access.play.store.read(cid))
    assert set(state.party.groups[0].actor_ids) == {"a", "b"}
    async with TestClient(
        TestServer(
            create_campaign_app(
                restarted.access, {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
            )
        )
    ) as client:
        for principal, actor in [("alice", "a"), ("bob", "b")]:
            response = await client.get(
                "/campaigns/" + cid, headers={"Authorization": f"Bearer {principal}-token"}
            )
            assert response.status == 200, await response.text()
            view = await response.json()
            assert view["actors"] == [actor]
            assert all(c["actor_id"] == actor for c in view["characters"])


async def test_late_generation_and_provider_failure_preserve_newer_draft(tmp_path: Path) -> None:
    from test_wave9 import FakeProvider

    from wayfarer.errors import ProviderError
    from wayfarer.orchestration.providers import Orchestrator, ProviderReply, ProviderRequest, Usage

    setup = service(tmp_path)
    cid = await ready(setup)
    entered, release = asyncio.Event(), asyncio.Event()

    class Delayed(FakeProvider):
        async def complete(self, request: ProviderRequest) -> ProviderReply:
            entered.set()
            await release.wait()
            return ProviderReply(payload_json=graph_fixture().model_dump_json(), usage=Usage())

    command = SetupCommand(id="generate", expected_revision=3, operation="edit")
    task = asyncio.create_task(
        setup.generate(cid, command, Orchestrator(setup.access, Delayed()), principal_id="alice")
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    await setup.execute(
        cid,
        command.model_copy(update={"id": "newer", "graph": graph_fixture()}),
        principal_id="alice",
    )
    release.set()
    with pytest.raises(ConflictError):
        await task
    saved = await setup.play.store.read(cid)

    class Failed(FakeProvider):
        async def complete(self, request: ProviderRequest) -> ProviderReply:
            raise ProviderError("offline")

    with pytest.raises(ProviderError):
        await setup.generate(
            cid,
            command.model_copy(update={"expected_revision": 4}),
            Orchestrator(setup.access, Failed(), attempts=1),
            principal_id="alice",
        )
    assert await setup.play.store.read(cid) == saved


async def test_generation_retry_uses_saved_receipt(tmp_path: Path) -> None:
    from test_wave9 import FakeProvider

    from wayfarer.orchestration.providers import Orchestrator

    setup = service(tmp_path)
    cid = await ready(setup)
    provider = FakeProvider(graph_fixture().model_dump_json())
    llm = Orchestrator(setup.access, provider)
    command = SetupCommand(id="generate", expected_revision=3, operation="edit")
    first = await setup.generate(cid, command, llm, principal_id="alice")
    second = await setup.generate(cid, command, llm, principal_id="alice")
    assert first == second and len(provider.requests) == 1


async def test_engine_ending_required_for_completion_and_archive(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await ready(setup)
    await setup.execute(
        cid,
        SetupCommand(id="activate", expected_revision=3, operation="activate"),
        principal_id="alice",
    )
    access = await setup.access.runtime(cid)
    await access.execute(
        cid,
        {
            "id": "travel",
            "expected_revision": 4,
            "kind": "travel_scene",
            "actor_id": "a",
            "exit_id": "to-alley",
        },
        principal_id="alice",
    )
    state = access.play._load(await access.play.store.read(cid))
    assert state.objectives.outcome != "ongoing"
    await setup.execute(
        cid,
        SetupCommand(id="complete", expected_revision=state.revision, operation="complete"),
        principal_id="alice",
    )
    state = access.play._load(await access.play.store.read(cid))
    assert state.lifecycle == "completed"
    await setup.execute(
        cid,
        SetupCommand(id="archive", expected_revision=state.revision, operation="archive"),
        principal_id="alice",
    )
    archived = await setup.play.store.read(cid)
    assert setup.load(archived).phase == "archived"
    assert archived["complete"]
    assert (await setup.play.store.replay(cid)) == archived


async def test_v1_reads_activated_runtime_and_rejects_paused_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.transport.v1.common import obj
    from wayfarer.transport.v1.http import SERVICE

    setup = service(tmp_path)
    cid = await ready(setup)
    async with TestClient(
        TestServer(create_campaign_app(setup.access, {"alice-token": "alice"}))
    ) as client:
        headers = {"Authorization": "Bearer alice-token"}
        response = await client.get("/api/v1/campaigns", headers=headers)
        assert response.status == 200
        assert (await response.json())["items"] == []
        await setup.execute(
            cid,
            SetupCommand(id="activate", expected_revision=3, operation="activate"),
            principal_id="alice",
        )
        v1 = client.app[SERVICE]
        async with v1.ledger.transaction() as tx:
            view = await v1.view(tx, cid, "alice")
            request = {
                "command_id": "8cfbf3e4-8d9f-4a88-95b2-e4ef9d8bd282",
                "actor_id": "a",
                "scene_id": "dock-scene",
                "expected_versions": {
                    "scene": view.scenes["dock-scene"]["version"],
                    "character": view.characters["a"]["version"],
                    "inventory": view.inventories["a"]["version"],
                },
                "intent": {"kind": "wait", "ticks": 1},
            }
        # Resolve explicitly so a pause racing an accepted action is deterministic.
        scheduled = v1.schedule
        monkeypatch.setattr(v1, "schedule", lambda _: None)
        response = await client.post(
            f"/api/v1/campaigns/{cid}/actions", json=request, headers=headers
        )
        assert response.status == 202, await response.text()
        action = obj(await response.json())
        await setup.execute(
            cid,
            SetupCommand(id="pause", expected_revision=4, operation="pause"),
            principal_id="alice",
        )
        await v1.resolve(str(action["id"]))
        state = (await setup.access.runtime(cid)).play._load(await setup.play.store.read(cid))
        assert state.resources.game_time == 0
        response = await client.get(f"/api/v1/campaigns/{cid}", headers=headers)
        assert response.status == 200
        assert (await response.json())["status"] == "paused"
        response = await client.post(
            f"/api/v1/campaigns/{cid}/actions",
            json={**request, "command_id": "8cfbf3e4-8d9f-4a88-95b2-e4ef9d8bd283"},
            headers=headers,
        )
        assert response.status == 409
        monkeypatch.setattr(v1, "schedule", scheduled)
