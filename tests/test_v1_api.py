"""Real aiohttp provider conformance with two authenticated perspectives."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from test_actions import Dice, actor_setup, campaign, engine, resource_seed, world

from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.resources import Owner
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.campaign_api import create_campaign_app
from wayfarer.transport.v1.common import Fault, Obj, array, obj, uid, validate
from wayfarer.transport.v1.http import SERVICE
from wayfarer.transport.v1.projection import Projector
from wayfarer.transport.v1.service import V1Service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def api(tmp_path: Path) -> AsyncIterator[tuple[str, str, V1Service]]:
    reducer = engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "engine.sqlite"), reducer, rng=Dice())
    initial = campaign(reducer)
    w = world()
    w = replace(
        w, entities=tuple(replace(e, location_id="far") if e.id == "b" else e for e in w.entities)
    )
    resources = resource_seed()
    resources = resources.model_copy(
        update={"owners": resources.owners + (Owner(actor_id="b", capacity=100),)}
    )
    await play.create(
        initial,
        w,
        resources,
        (actor_setup(), actor_setup().model_copy(update={"actor_id": "b", "aware_of": ()})),
        (
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    app = create_campaign_app(
        CampaignAccess(play),
        {"alice-key": "alice", "bob-key": "bob", "gm-key": "gm", "new-key": "new"},
        v1_origins=frozenset({"https://game.example"}),
        v1_allow_no_origin=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}", initial["id"], app[SERVICE]
    finally:
        await runner.cleanup()


async def get(client: aiohttp.ClientSession, url: str, schema: str) -> Obj:
    async with client.get(url) as response:
        value = obj(await response.json())
        assert response.status == 200, value
        validate(schema, value)
        return value


async def command(
    client: aiohttp.ClientSession,
    root: str,
    *,
    actor: str = "a",
    scene: str = "dock",
    kind: str = "use_item",
) -> Obj:
    character = await get(client, f"{root}/characters/{actor}", "Character")
    inventory = await get(client, f"{root}/characters/{actor}/inventory", "Inventory")
    view = await get(client, f"{root}/scenes/{scene}", "Scene")
    intent: Obj = (
        {"kind": "use_item", "item_id": "potions", "quantity": 1}
        if kind == "use_item"
        else {"kind": "wait", "ticks": 1}
    )
    return {
        "command_id": uid(),
        "actor_id": actor,
        "scene_id": scene,
        "expected_versions": {
            "scene": view["version"],
            "character": character["version"],
            "inventory": inventory["version"],
        },
        "intent": intent,
    }


async def finish(
    client: aiohttp.ClientSession, root: str, action: Obj, status: str = "succeeded"
) -> Obj:
    async with asyncio.timeout(5):
        while True:
            action = await get(client, f"{root}/actions/{action['id']}", "Action")
            if action["status"] in ("succeeded", "rejected", "cancelled", "needs_clarification"):
                assert action["status"] == status, action
                return action
            await asyncio.sleep(0.01)


async def test_headers_errors_body_limits_and_filtered_reads(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, _ = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        for path, schema in [
            ("/api/v1/me", "Principal"),
            ("/api/v1/campaigns", "CampaignPage"),
            (f"/api/v1/campaigns/{cid}", "Campaign"),
            (f"/api/v1/campaigns/{cid}/members", "MembershipPage"),
            (f"/api/v1/campaigns/{cid}/characters", "CharacterPage"),
            (f"/api/v1/campaigns/{cid}/scenes", "ScenePage"),
        ]:
            await get(client, base + path, schema)
        for path in ("characters/b", "characters/b/inventory", "scenes/far", "actions/missing"):
            async with client.get(
                root + "/" + path, headers={"X-Request-ID": "attacker"}
            ) as response:
                value = obj(await response.json())
                assert response.status == 404
                validate("Error", value)
                assert value["request_id"] == response.headers["X-Request-ID"] != "attacker"
                assert response.headers["Cache-Control"] == "no-store"
        valid = await command(client, root)
        for payload, status, headers in [
            ("{", 400, {"Content-Type": "application/json"}),
            ("x" * 32001, 413, {"Content-Type": "application/json"}),
            ("{}", 415, {"Content-Type": "text/plain"}),
        ]:
            async with client.post(root + "/actions", data=payload, headers=headers) as response:
                assert response.status == status
                validate("Error", await response.json())
        async with client.post(root + "/actions", json={**valid, "hp": 100}) as response:
            assert response.status == 400
        async with client.get(root, headers={"Authorization": "Bearer bad"}) as response:
            assert response.status == 401 and response.headers["WWW-Authenticate"] == "Bearer"


async def test_concurrent_retry_stale_and_restart_receipts(api: tuple[str, str, V1Service]) -> None:
    base, cid, service = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        request = await command(client, root)

        async def submit() -> Obj:
            async with client.post(root + "/actions", json=request) as response:
                assert response.status == 202, await response.text()
                return validate("Action", await response.json())

        replies = await asyncio.gather(*(submit() for _ in range(5)))
        assert len({str(r["id"]) for r in replies}) == 1
        result = await finish(client, root, replies[0])
        inventory = await get(client, root + "/characters/a/inventory", "Inventory")
        assert (
            next(obj(i)["quantity"] for i in array(inventory["items"]) if obj(i)["id"] == "potions")
            == 4
        )
        async with client.post(
            root + "/actions", json={**request, "intent": {"kind": "wait", "ticks": 1}}
        ) as response:
            assert (
                response.status == 409 and (await response.json())["code"] == "idempotency_conflict"
            )
        async with client.post(
            root + "/actions", json={**request, "command_id": uid()}
        ) as response:
            assert response.status == 409
        restarted = V1Service(service.play, service.ledger.path)
        await restarted.start()
        try:
            replay = await restarted.submit(
                "alice", cid, f"/api/v1/campaigns/{cid}/actions", request
            )
            assert replay == result
        finally:
            await restarted.close()
        async with client.post(
            root + f"/actions/{result['id']}/cancellation",
            json={"command_id": uid(), "expected_action_version": result["version"]},
        ) as response:
            assert response.status == 409


async def connect(
    client: aiohttp.ClientSession,
    base: str,
    cid: str,
    *,
    token: str = "alice-key",
    actor: str = "a",
    scene: str = "dock",
    resume: Obj | None = None,
) -> tuple[aiohttp.ClientWebSocketResponse, list[Obj]]:
    ws = await client.ws_connect(base + "/api/v1/live", protocols=("wayfarer.live.v1",))
    await ws.send_json({"type": "authenticate", "request_id": uid(), "credential": token})
    validate("ServerMessage", await ws.receive_json(), live=True)
    await ws.send_json(
        {
            "type": "subscribe",
            "request_id": uid(),
            "subscription_id": uid(),
            "scope": {"campaign_id": cid, "scene_id": scene, "actor_id": actor},
            "resume": resume,
        }
    )
    frames: list[Obj] = []
    async with asyncio.timeout(5):
        while True:
            frame = validate("ServerMessage", await ws.receive_json(), live=True)
            frames.append(frame)
            if frame["type"] in ("stream.ready", "stream.reset", "error"):
                break
    return ws, frames


async def test_atomic_snapshot_replay_and_private_cursor(api: tuple[str, str, V1Service]) -> None:
    base, cid, _ = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        ws, frames = await connect(client, base, cid)
        assert [f["type"] for f in frames] == ["subscribed", "snapshot.begin"] + [
            "snapshot.resource"
        ] * 4 + ["snapshot.end", "stream.ready"]
        ready = frames[-1]
        request = await command(client, root)
        async with client.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        await finish(client, root, action)
        changes: list[Obj] = []
        async with asyncio.timeout(5):
            while not any(
                f["type"] == "action.updated" and obj(f["action"])["status"] == "succeeded"
                for f in changes
            ):
                frame = validate("ServerMessage", await ws.receive_json(), live=True)
                changes.append(frame)
        await ws.close()
        resume = {"cursor": ready["cursor"], "visibility_epoch": ready["visibility_epoch"]}
        ws2, replay = await connect(client, base, cid, resume=resume)
        assert replay[0]["mode"] == "replay"
        assert not any(f["type"] == "snapshot.begin" for f in replay)
        assert any(f.get("event_id") == changes[-1].get("event_id") for f in replay)
        await ws2.close()
        foreign, frames = await connect(
            client, base, cid, token="bob-key", actor="b", scene="far", resume=resume
        )
        assert frames[-1]["type"] == "error" and frames[-1]["code"] == "invalid_cursor"
        await foreign.close()


async def test_hidden_changes_do_not_advance_alice_versions_or_cursor(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, service = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with (
        aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as alice,
        aiohttp.ClientSession(headers={"Authorization": "Bearer bob-key"}) as bob,
    ):
        before = await command(alice, root)
        ws, frames = await connect(alice, base, cid)
        await ws.close()
        request = await command(bob, root, actor="b", scene="far", kind="wait")
        async with bob.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        await finish(bob, root, action)
        after = await command(alice, root)
        assert before["expected_versions"] == after["expected_versions"]
        ready = frames[-1]
        replay, frames = await connect(
            alice,
            base,
            cid,
            resume={"cursor": ready["cursor"], "visibility_epoch": ready["visibility_epoch"]},
        )
        assert [f["type"] for f in frames] == ["subscribed", "stream.ready"]
        assert frames[-1]["cursor"] == ready["cursor"]
        await replay.close()
        assert (await service.play.store.read(cid))["revision"] == 1


async def test_revocation_barrier_clears_stream_and_receipt_access(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, service = api
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        ws, _ = await connect(client, base, cid)
        raw = await service.play.store.read(cid)

        def revoke(campaign: Campaign) -> CommandReceipt:
            state = service.play._load(campaign)
            state = state.model_copy(
                update={
                    "members": tuple(m for m in state.members if m.principal_id != "alice"),
                    "revision": state.revision + 1,
                    "resources": state.resources.model_copy(
                        update={"revision": state.revision + 1}
                    ),
                }
            )
            campaign["play_json"], campaign["revision"] = state.model_dump_json(), state.revision
            return CommandReceipt(action="v1-membership", outcome="revoked")

        await service.play.store.commit_turn(cid, uid(), raw["revision"], "revoke", revoke)
        async with asyncio.timeout(5):
            frame = validate("ServerMessage", await ws.receive_json(), live=True)
        assert frame["type"] == "subscription.revoked"
        assert set(frame) == {"type", "protocol_version", "subscription_id"}
        async with client.get(f"{base}/api/v1/campaigns/{cid}") as response:
            assert response.status == 404
        await ws.close()


async def test_runtime_schema_copies_match_frozen_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("openapi.json", "schemas.json", "events.schema.json", "engine-events.schema.json"):
        assert json.loads((root / "contracts/v1" / name).read_text()) == json.loads(
            (root / "src/wayfarer/transport/v1" / name).read_text()
        )


async def test_clarification_and_narration_failure_keep_committed_state(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, service = api

    async def interpreter(context: Obj, text: str) -> Obj:
        assert "Hidden" not in json.dumps(context)
        if text == "Which item?":
            return {
                "clarification": {
                    "id": "choose",
                    "prompt": "Choose an item.",
                    "choices": [{"id": "potion", "label": "Use potion"}],
                    "allows_text": False,
                }
            }
        return {"kind": "use_item", "item_id": "potions", "quantity": 1}

    async def failed_narrator(context: Obj, action: Obj) -> str:
        raise RuntimeError("provider unavailable")

    service.interpret, service.narrate = interpreter, failed_narrator
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        request = await command(client, root)
        request["intent"] = {"kind": "text", "text": "Which item?"}
        async with client.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        pending = await finish(client, root, action, "needs_clarification")
        answer = {
            "command_id": uid(),
            "expected_action_version": pending["version"],
            "expected_versions": request["expected_versions"],
            "clarification_id": "choose",
            "answer": {"choice_id": "potion"},
        }
        for _ in range(2):
            async with client.post(
                root + f"/actions/{action['id']}/clarifications", json=answer
            ) as response:
                assert response.status == 202, await response.text()
        await finish(client, root, action)
        ws, _ = await connect(client, base, cid)
        async with asyncio.timeout(5):
            started = validate("ServerMessage", await ws.receive_json(), live=True)
            ended = validate("ServerMessage", await ws.receive_json(), live=True)
        assert started["type"] == "narration.started"
        assert ended["type"] == "narration.ended" and ended["status"] == "failed"
        assert (await get(client, root + f"/actions/{action['id']}", "Action"))[
            "status"
        ] == "succeeded"
        await ws.close()


async def test_single_use_invitation_and_cross_operation_receipt_conflict(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, _ = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with (
        aiohttp.ClientSession(headers={"Authorization": "Bearer gm-key"}) as gm,
        aiohttp.ClientSession(headers={"Authorization": "Bearer new-key"}) as newcomer,
    ):
        campaign = await get(gm, root, "Campaign")
        request = {
            "command_id": uid(),
            "expected_membership_version": obj(campaign["membership"])["version"],
            "role": "player",
            "expires_in_seconds": 60,
        }
        async with gm.post(root + "/invitations", json=request) as response:
            assert response.status == 201, await response.text()
            invite = validate("Invitation", await response.json())
        async with gm.post(root + "/invitations", json=request) as response:
            assert invite == await response.json()
        redeem = {"command_id": uid(), "token": invite["token"]}
        for _ in range(2):
            async with newcomer.post(base + "/api/v1/invitations/redeem", json=redeem) as response:
                assert response.status == 200, await response.text()
                member = validate("Membership", await response.json())
                assert member["role"] == "player" and member["actor_ids"] == []
        async with gm.post(
            base + "/api/v1/invitations/redeem",
            json={"command_id": uid(), "token": invite["token"]},
        ) as response:
            assert response.status == 404


async def test_recover_after_engine_commit_before_receipt_finalization(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, service = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        request = await command(client, root)
        request["intent"] = {"kind": "inspect", "target_id": "chest"}
        async with client.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        result = await finish(client, root, action)
        aid = str(action["id"])
        async with service.ledger.transaction() as tx:
            record = await tx.get("action:" + aid)
            assert record is not None
            service.transition(record, "resolving", at=tx.instant.isoformat())
            await tx.put("action:" + aid, record)
        restarted = V1Service(service.play, service.ledger.path)
        await restarted.start()
        try:
            restored = await finish(client, root, action)
            assert restored["resolution"] == result["resolution"]
            assert (await service.play.store.read(cid))["revision"] == 1
        finally:
            await restarted.close()


async def test_origin_and_unknown_query_fail_closed(api: tuple[str, str, V1Service]) -> None:
    base, _, _ = api
    async with aiohttp.ClientSession() as client:
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
            await client.ws_connect(
                base + "/api/v1/live",
                protocols=("wayfarer.live.v1",),
                origin="https://evil.example",
            )
        assert exc.value.status == 403
        with pytest.raises(aiohttp.WSServerHandshakeError) as exc:
            await client.ws_connect(
                base + "/api/v1/live?token=secret", protocols=("wayfarer.live.v1",)
            )
        assert exc.value.status == 400


async def test_gm_inspection_and_hidden_target_validation(api: tuple[str, str, V1Service]) -> None:
    base, cid, _ = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with (
        aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as alice,
        aiohttp.ClientSession(headers={"Authorization": "Bearer gm-key"}) as gm,
    ):
        await get(gm, root + "/scenes/alley", "Scene")
        await get(gm, root + "/characters/b", "Character")
        request = await command(alice, root)
        async with alice.post(
            root + "/actions",
            json={**request, "intent": {"kind": "inspect", "target_id": "hidden"}},
        ) as response:
            assert response.status == 404
        async with alice.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        result = await finish(alice, root, action)
        assert (await get(gm, root + f"/actions/{result['id']}", "Action"))["id"] == result["id"]
        async with gm.post(root + "/actions", json=request) as response:
            assert response.status == 403
        async with alice.get(root + "/characters/a?search=hidden") as response:
            assert response.status == 400


async def test_cancel_pending_interpretation_and_foreign_command_id(
    api: tuple[str, str, V1Service],
) -> None:
    base, cid, service = api
    entered, release = asyncio.Event(), asyncio.Event()

    async def interpret(context: Obj, text: str) -> Obj:
        entered.set()
        await release.wait()
        return {"kind": "wait", "ticks": 1}

    service.interpret = interpret
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        request = await command(client, root)
        request["intent"] = {"kind": "text", "text": "Wait"}
        async with client.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        await asyncio.wait_for(entered.wait(), 3)
        cancel = {"command_id": uid(), "expected_action_version": action["version"]}
        async with client.post(
            root + f"/actions/{action['id']}/cancellation", json=cancel
        ) as response:
            assert response.status == 200 and (await response.json())["status"] == "cancelled"
        release.set()
        await finish(client, root, action, "cancelled")
        async with client.post(
            root + "/actions", json={**request, "command_id": cancel["command_id"]}
        ) as response:
            assert (
                response.status == 409 and (await response.json())["code"] == "idempotency_conflict"
            )
        assert (await service.play.store.read(cid))["revision"] == 0


async def test_bound_paginated_snapshots_expire(api: tuple[str, str, V1Service]) -> None:
    base, cid, service = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer gm-key"}) as client:
        first = await get(client, root + "/characters?limit=1", "CharacterPage")
        cursor = str(first["next_cursor"])
        second = await get(client, root + "/characters?limit=1&cursor=" + cursor, "CharacterPage")
        assert obj(array(first["items"])[0])["id"] != obj(array(second["items"])[0])["id"]
        async with client.get(root + "/scenes?cursor=" + cursor) as response:
            assert response.status == 400
        async with service.ledger.transaction() as tx:
            record = await tx.get("page:" + cursor)
            assert record is not None
            record["expires"] = 0
            await tx.put("page:" + cursor, record)
        async with client.get(root + "/characters?cursor=" + cursor) as response:
            assert response.status == 410 and (await response.json())["code"] == "cursor_expired"


async def test_legacy_routes_are_disabled_by_default(api: tuple[str, str, V1Service]) -> None:
    base, cid, _ = api
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        for path in (f"/campaigns/{cid}", f"/campaigns/{cid}/events"):
            async with client.get(base + path) as response:
                assert response.status == 404


async def test_provider_bridge_uses_only_scoped_context(api: tuple[str, str, V1Service]) -> None:
    from wayfarer.orchestration.providers import Orchestrator, ProviderReply, ProviderRequest, Usage
    from wayfarer.transport.v1.provider import bind_provider

    _, cid, service = api

    class Provider:
        async def complete(self, request: ProviderRequest) -> object:
            assert "Hidden" not in request.context_json
            assert "revision" not in request.context_json
            value = (
                {"kind": "wait", "ticks": 1}
                if request.operation == "intent"
                else {"text": "A moment passes."}
            )
            return ProviderReply(payload_json=json.dumps(value), usage=Usage())

    bind_provider(service, Orchestrator(CampaignAccess(service.play), Provider()))
    async with service.ledger.transaction() as tx:
        view = await service.view(tx, cid, "alice")
    context: Obj = {
        "campaign": view.campaign,
        "scene": view.scenes["dock"],
        "character": view.characters["a"],
    }
    assert service.interpret is not None and service.narrate is not None
    from wayfarer.transport.v1.service import Interpretation

    interpreted = await service.interpret(context, "Wait")
    assert isinstance(interpreted, Interpretation)
    assert interpreted.intent == {"kind": "wait", "ticks": 1}
    assert interpreted.origin.proposal_type == "v1.Intent"
    assert (
        await service.narrate(
            context, {"actor_id": "a", "resolution": {"summary": "Wait complete"}}
        )
        == "A moment passes."
    )


async def test_configured_scene_travel_uses_scene_engine(tmp_path: Path) -> None:
    from test_scenes import setup

    cid, play, _ = await setup(tmp_path)
    service = V1Service(play, tmp_path / "travel-v1.sqlite")
    await service.start()
    try:
        async with service.ledger.transaction() as tx:
            view = await service.view(tx, cid, "a")
        exits = [
            obj(observation)
            for observation in array(view.scenes["dock-scene"]["observations"])
            if obj(observation).get("description") == "Known scene exit"
        ]
        assert exits == [
            {"id": "alley-scene", "label": "The Alley", "description": "Known scene exit"}
        ]
        request: Obj = {
            "command_id": uid(),
            "actor_id": "a",
            "scene_id": "dock-scene",
            "expected_versions": {
                "scene": view.scenes["dock-scene"]["version"],
                "character": view.characters["a"]["version"],
            },
            "intent": {"kind": "move", "destination_id": "alley-scene"},
        }
        action = await service.submit("a", cid, f"/api/v1/campaigns/{cid}/actions", request)
        async with asyncio.timeout(5):
            while True:
                async with service.ledger.transaction() as tx:
                    record = await tx.get("action:" + str(action["id"]))
                assert record is not None
                if obj(record["wire"])["status"] in ("succeeded", "rejected"):
                    assert obj(record["wire"])["status"] == "succeeded", record
                    break
                await asyncio.sleep(0.01)
        state = play._load(await play.store.read(cid))
        assert state.actor_scenes[0].scene_id == "alley-scene"
        assert state.revision == 1
        assert ("a", "alley-seen") in state.world.knowledge
        async with service.ledger.transaction() as tx:
            receipt = await service.action(tx, str(action["id"]), cid, "a")
            assert obj(receipt["wire"])["status"] == "succeeded"
            assert obj(receipt["wire"])["scene_id"] == "dock-scene"
            assert receipt["receipt_scene_id"] == "alley-scene"
            with pytest.raises(Fault):
                await service.action(tx, str(action["id"]), cid, "another-player")
            # The journey left the actor in the destination, which is the only
            # scene still readable. The turn has to be listed there, or the
            # player's own committed turn is invisible and cannot be narrated
            # from the scene they are standing in (#294).
            view = await service.view(tx, cid, "a")
            assert sorted(view.scenes) == ["alley-scene"]
            listed = await service.actions(tx, cid, "a", "alley-scene")
            assert [str(x["id"]) for x in listed] == [str(action["id"])]
    finally:
        await service.close()


async def test_actions_read_in_creation_order_across_pages(
    api: tuple[str, str, V1Service],
) -> None:
    """A session log is read in the order it happened, not in identifier order (#295)."""
    base, cid, service = api
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        submitted: list[str] = []
        for _ in range(3):
            request = await command(client, root, kind="wait")
            async with client.post(root + "/actions", json=request) as response:
                assert response.status == 202, await response.text()
                action = validate("Action", await response.json())
            await finish(client, root, action)
            submitted.append(str(action["id"]))
        # Identifiers are random, so stamp a chronology that disagrees with them
        # and with the order the turns were taken.
        stamps = [
            "2026-09-08T09:00:00.000Z",
            "2026-09-08T08:00:00.000Z",
            "2026-09-08T10:00:00.000Z",
        ]
        async with service.ledger.transaction() as tx:
            for aid, at in zip(submitted, stamps, strict=True):
                record = await tx.get("action:" + aid)
                assert record is not None
                record["wire"] = validate("Action", {**obj(record["wire"]), "created_at": at})
                await tx.put("action:" + aid, record)
        expected = [aid for _, aid in sorted(zip(stamps, submitted, strict=True))]
        page = await get(client, root + "/actions?scene_id=dock", "ActionPage")
        assert [str(obj(item)["id"]) for item in array(page["items"])] == expected
        # The same order survives a page boundary rather than being resorted per page.
        paged: list[str] = []
        query = "?scene_id=dock&limit=1"
        while True:
            page = await get(client, root + "/actions" + query, "ActionPage")
            paged.extend(str(obj(item)["id"]) for item in array(page["items"]))
            if not page["next_cursor"]:
                break
            query = f"?scene_id=dock&limit=1&cursor={page['next_cursor']}"
        assert paged == expected


async def test_capabilities_name_only_the_actions_the_engine_executes(
    api: tuple[str, str, V1Service], tmp_path: Path
) -> None:
    """Advertised capabilities conform: absent ones must not show active controls."""
    base, cid, _ = api
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        record = await get(client, f"{base}/api/v1/campaigns/{cid}", "Campaign")
    advertised = [str(x) for x in array(record["capabilities"])]
    # This scenario authors an inspect check for the chest and none for Iven, so
    # only the chest is named; the potion makes item use conform.
    assert "actions.inspect" in advertised
    assert "actions.inspect:chest" in advertised
    assert "actions.inspect:b" not in advertised
    assert "actions.use_item" in advertised
    # A target the caller cannot see is never named, inspectable or not.
    assert "actions.inspect:hidden" not in advertised
    # No interpretation provider is configured for this app.
    assert "actions.text" not in advertised
    reducer = engine()
    bare = ActionEngine(reducer.reviewer, reducer.resources, ActionRules(id="actions", version=1))
    play = PlayService(AsyncSQLiteStore(tmp_path / "bare.sqlite"), bare, rng=Dice())
    initial = campaign(bare)
    await play.create(
        initial,
        world(),
        resource_seed(),
        (actor_setup(),),
        (CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),),
    )
    view = Projector(play, "secret").make(
        await play.store.read(initial["id"]), "alice", "1970-01-01T00:00:00Z"
    )
    # Without authored checks or consumables both kinds answer unsupported_action.
    assert [str(x) for x in array(view.campaign["capabilities"])] == [
        "actions.move",
        "actions.wait",
    ]


async def test_provider_diagnostic_survives_action_receipt_without_secrets(
    api: tuple[str, str, V1Service],
) -> None:
    from wayfarer.orchestration.codex import CodexAuthenticationError

    base, cid, service = api

    async def unavailable(context: Obj, text: str) -> Obj:
        error = CodexAuthenticationError("SECRET_TOKEN private SDK response")
        error.stage = "account"
        raise error

    service.interpret = unavailable
    root = f"{base}/api/v1/campaigns/{cid}"
    async with aiohttp.ClientSession(headers={"Authorization": "Bearer alice-key"}) as client:
        request = await command(client, root)
        request["intent"] = {"kind": "text", "text": "Search the dock"}
        async with client.post(root + "/actions", json=request) as response:
            action = obj(await response.json())
        action = await finish(client, root, action, "rejected")
        error = obj(action["error"])
        assert error["code"] == "service_unavailable"
        assert error["retryable"] is False
        assert "codex_login_required" in str(error["message"])
        assert "account check" in str(error["message"])
        assert "SECRET_TOKEN" not in json.dumps(action)
        assert (await get(client, f"{root}/actions/{action['id']}", "Action"))["error"] == error
        assert (await service.play.store.read(cid))["revision"] == 0
