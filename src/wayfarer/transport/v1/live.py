"""Authenticated scoped snapshots, replay, barriers and bounded live delivery."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiohttp import WSMsgType, web

from . import outbox
from .common import (
    Fault,
    Obj,
    array,
    encoded,
    obj,
    uid,
    validate,
)
from .keys import NO_ORIGIN, ORIGINS, SERVICE, TOKENS, identity
from .outbox import sync

CONNECTIONS = web.AppKey("v1-connections", dict[str, int])


@dataclass
class Subscription:
    scope: Obj
    epoch: str
    policy: str
    cursor: str
    sent: list[tuple[str, int]] = field(default_factory=list)
    narration: Obj | None = None


async def live(request: web.Request) -> web.WebSocketResponse:
    origin = request.headers.get("Origin")
    if (origin is None and not request.app[NO_ORIGIN]) or (
        origin is not None and origin not in request.app[ORIGINS]
    ):
        raise Fault(403, "forbidden")
    if request.query or "wayfarer.live.v1" not in [
        x.strip() for x in request.headers.get("Sec-WebSocket-Protocol", "").split(",")
    ]:
        raise Fault(400, "invalid_request")
    counters = request.app[CONNECTIONS]
    ip = "ip:" + str(request.remote)
    if counters.get(ip, 0) >= 8:
        raise Fault(429, "rate_limited")
    counters[ip] = counters.get(ip, 0) + 1
    ws = web.WebSocketResponse(protocols=("wayfarer.live.v1",), compress=False, max_msg_size=32000)
    await ws.prepare(request)
    service = request.app[SERVICE]
    principal = ""
    principal_admitted = False
    credential = ""
    subscriptions: dict[str, Subscription] = {}
    used: set[str] = set()
    receipts: dict[str, tuple[str, Obj]] = {}
    rate: list[float] = []
    tasks: set[asyncio.Task[None]] = set()

    async def send(value: Obj) -> None:
        message = {"protocol_version": "1.0.0", **value}
        validate("ServerMessage", message, live=True)
        raw = encoded(message)
        if len(raw.encode()) > 8388608:
            raise Fault(503, "snapshot_too_large")
        try:
            async with asyncio.timeout(10):
                await ws.send_str(raw)
        except TimeoutError as exc:
            raise ConnectionError("Live delivery deadline exceeded") from exc

    async def barrier(sid: str) -> bool:
        sub = subscriptions.get(sid)
        if sub is None:
            return False
        try:
            identity(request.app[TOKENS], credential)
        except Fault:
            subscriptions.clear()
            await ws.close(code=4401)
            return False
        try:
            async with service.ledger.transaction() as tx:
                current = await service.view(
                    tx, str(sub.scope["campaign_id"]), principal, str(sub.scope["actor_id"])
                )
                service.scope(current, str(sub.scope["actor_id"]), str(sub.scope["scene_id"]))
        except Fault:
            subscriptions.pop(sid, None)
            await send({"type": "subscription.revoked", "subscription_id": sid})
            return False
        if current.policy != sub.policy:
            subscriptions.pop(sid, None)
            await send(
                {
                    "type": "stream.reset",
                    "subscription_id": sid,
                    "reason": "visibility_changed",
                    "retry_after_ms": 0,
                }
            )
            return False
        return True

    async def scoped(sid: str, value: Obj) -> bool:
        if not await barrier(sid):
            return False
        sub = subscriptions[sid]
        if value.get("type") == "narration.delta" and (
            sub.narration is None or sub.narration["status"] != "active"
        ):
            return False
        await send(
            {"subscription_id": sid, "scope": sub.scope, "visibility_epoch": sub.epoch, **value}
        )
        return True

    async def tail(sid: str, checkpoint: Obj) -> None:
        sub = subscriptions.get(sid)
        if sub is None:
            return
        if checkpoint["epoch"] != sub.epoch:
            subscriptions.pop(sid, None)
            await send(
                {
                    "type": "stream.reset",
                    "subscription_id": sid,
                    "reason": "visibility_changed",
                    "retry_after_ms": 0,
                }
            )
            return
        if checkpoint["cursor"] == sub.cursor:
            return
        events = [obj(e) for e in array(checkpoint["events"])]
        start = next((i for i, e in enumerate(events) if e["previous_cursor"] == sub.cursor), None)
        if start is None:
            subscriptions.pop(sid, None)
            await send(
                {
                    "type": "stream.reset",
                    "subscription_id": sid,
                    "reason": "cursor_expired",
                    "retry_after_ms": 0,
                }
            )
            return
        for event in events[start:]:
            wire = {k: v for k, v in event.items() if k != "_at"}
            size = len(
                encoded(
                    {
                        "protocol_version": "1.0.0",
                        "subscription_id": sid,
                        "scope": sub.scope,
                        "visibility_epoch": sub.epoch,
                        **wire,
                    }
                ).encode()
            )
            if len(sub.sent) >= 256 or sum(n for _, n in sub.sent) + size > 16777216:
                subscriptions.pop(sid, None)
                await send(
                    {
                        "type": "stream.reset",
                        "subscription_id": sid,
                        "reason": "backpressure",
                        "retry_after_ms": 500,
                    }
                )
                return
            if not await scoped(sid, wire):
                return
            sub.cursor = str(event["cursor"])
            sub.sent.append((sub.cursor, size))

    async def narration(sid: str, action: Obj, context: Obj) -> None:
        sub = subscriptions.get(sid)
        if sub is None:
            return
        nid = uid()
        sub.narration = {"narration_id": nid, "status": "active", "chunk_count": 0}
        started = {
            "type": "narration.started",
            "narration_id": nid,
            "action_id": action["id"],
            "command_id": action["command_id"],
            "basis": "committed",
            "voice_session_id": None,
        }
        if not await scoped(sid, started):
            return
        status = "failed"
        try:
            if service.narrate is not None:
                text = await outbox.narrate(service, principal, action, context)
                if sub.narration["status"] != "active":
                    return
                if not text or len(text) > 4000:
                    raise ValueError("Invalid narration")
                if not await scoped(
                    sid, {"type": "narration.delta", "narration_id": nid, "index": 0, "text": text}
                ):
                    return
                sub.narration["chunk_count"] = 1
                status = "completed"
        except Exception:
            status = "failed"
        if sub.narration["status"] == "active":
            sub.narration["status"] = status
            await scoped(sid, {"type": "narration.ended", **sub.narration, "request_id": None})

    try:
        async with asyncio.timeout(5):
            first = await ws.receive()
        if first.type != WSMsgType.TEXT:
            await ws.close(code=1003)
            return ws
        auth = validate("ClientMessage", json.loads(first.data), live=True)
        if auth["type"] != "authenticate":
            raise Fault(401, "unauthenticated")
        credential = str(auth["credential"])
        principal = identity(request.app[TOKENS], credential)
        pkey = "principal:" + principal
        if counters.get(pkey, 0) >= 4:
            raise Fault(429, "rate_limited")
        counters[pkey] = counters.get(pkey, 0) + 1
        principal_admitted = True
        rate.append(time.monotonic())
        receipts[str(auth["request_id"])] = (encoded(auth), {})
        expiry = time.time() + 3600
        await send(
            {
                "type": "authenticated",
                "request_id": auth["request_id"],
                "connection_id": uid(),
                "principal_id": principal,
                "expires_at": datetime.fromtimestamp(expiry, UTC)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        )
        heartbeat = time.monotonic() + 15
        nonce: str | None = None
        deadline = 0.0
        while not ws.closed:
            if time.time() >= expiry:
                await ws.close(code=4401)
                break
            identity(request.app[TOKENS], credential)
            now = time.monotonic()
            if nonce is not None and now >= deadline:
                await ws.close(code=4408)
                break
            if now >= heartbeat:
                nonce = secrets.token_urlsafe(24)
                await send({"type": "heartbeat.ping", "nonce": nonce})
                deadline, heartbeat = now + 10, now + 15
            try:
                incoming = await ws.receive(timeout=0.1)
            except TimeoutError:
                incoming = None
            if incoming is not None:
                if incoming.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
                if incoming.type != WSMsgType.TEXT:
                    await ws.close(code=1003)
                    break
                rate = [t for t in rate if now - t < 60]
                if len(rate) >= 120:
                    raise Fault(429, "rate_limited")
                rate.append(now)
                message = validate("ClientMessage", json.loads(incoming.data), live=True)
                kind = str(message["type"])
                rid = str(message["request_id"])
                sid = str(message.get("subscription_id", ""))
                fingerprint = encoded(message)
                if kind == "authenticate":
                    raise Fault(400, "invalid_message")
                old = receipts.get(rid)
                if old:
                    if old[0] != fingerprint:
                        raise Fault(400, "invalid_message")
                    # Recheck before repeating any scoped control metadata.
                    if sid in subscriptions and await barrier(sid):
                        await send(old[1])
                    elif old[1]["type"] == "unsubscribed":
                        await send(old[1])
                    continue
                if kind == "authenticate":
                    raise Fault(400, "invalid_message")
                if kind == "heartbeat.pong":
                    if nonce == message["nonce"]:
                        nonce = None
                    continue
                if kind == "ack":
                    sub = subscriptions.get(sid)
                    if sub is None:
                        raise Fault(400, "invalid_message")
                    cursor = str(message["cursor"])
                    positions = [c for c, _ in sub.sent]
                    if cursor in positions:
                        sub.sent = sub.sent[positions.index(cursor) + 1 :]
                    elif cursor != sub.cursor:
                        raise Fault(400, "invalid_cursor")
                    continue
                if kind == "unsubscribe":
                    subscriptions.pop(sid, None)
                    reply: Obj = {"type": "unsubscribed", "subscription_id": sid, "request_id": rid}
                    receipts[rid] = (fingerprint, reply)
                    await send(reply)
                    continue
                if kind == "narration.interrupt":
                    sub = subscriptions.get(sid)
                    if (
                        sub is None
                        or sub.narration is None
                        or sub.narration["narration_id"] != message["narration_id"]
                    ):
                        await send(
                            {
                                "type": "error",
                                "request_id": rid,
                                "subscription_id": sid,
                                "code": "not_found",
                                "retry_after_ms": None,
                            }
                        )
                        continue
                    if sub.narration["status"] == "active":
                        sub.narration["status"] = "interrupted"
                    reply = {"type": "narration.ended", **sub.narration, "request_id": rid}
                    if await scoped(sid, reply):
                        receipts[rid] = (
                            fingerprint,
                            {
                                "subscription_id": sid,
                                "scope": sub.scope,
                                "visibility_epoch": sub.epoch,
                                **reply,
                            },
                        )
                    continue
                if kind == "subscribe":
                    if sid in used or len(subscriptions) >= 4:
                        raise Fault(400, "invalid_message")
                    used.add(sid)
                    scope = obj(message["scope"])
                    try:
                        async with service.ledger.transaction() as tx:
                            view, checkpoint = await sync(service, tx, principal, scope)
                            resume = message["resume"]
                            if resume is not None:
                                res = obj(resume)
                                cursor_record = await tx.get("cursor:" + str(res["cursor"]))
                                if not cursor_record or cursor_record["binding"] != encoded(
                                    [principal, scope]
                                ):
                                    raise Fault(400, "invalid_cursor")
                                reason = (
                                    "visibility_changed"
                                    if res["visibility_epoch"] != checkpoint["epoch"]
                                    else "cursor_expired"
                                    if float(str(cursor_record["at"])) < time.time() - 1200
                                    else None
                                )
                                if reason:
                                    await send(
                                        {
                                            "type": "stream.reset",
                                            "subscription_id": sid,
                                            "reason": reason,
                                            "retry_after_ms": 0,
                                        }
                                    )
                                    continue
                            sub = Subscription(
                                scope,
                                str(checkpoint["epoch"]),
                                view.policy,
                                str(obj(resume)["cursor"]) if resume else str(checkpoint["cursor"]),
                            )
                    except Fault as exc:
                        await send(
                            {
                                "type": "error",
                                "request_id": rid,
                                "subscription_id": sid,
                                "code": "invalid_cursor"
                                if exc.code == "invalid_cursor"
                                else "not_found",
                                "retry_after_ms": None,
                            }
                        )
                        continue
                    subscriptions[sid] = sub
                    reply = {
                        "type": "subscribed",
                        "subscription_id": sid,
                        "scope": scope,
                        "visibility_epoch": sub.epoch,
                        "mode": "replay" if resume else "snapshot",
                        "request_id": rid,
                    }
                    receipts[rid] = (fingerprint, reply)
                    if not await barrier(sid):
                        continue
                    await send(reply)
                    resources = array(checkpoint["resources"])
                    if not resume:
                        if len(resources) > 10000 or len(encoded(resources).encode()) > 33554432:
                            subscriptions.pop(sid, None)
                            await send(
                                {
                                    "type": "error",
                                    "request_id": rid,
                                    "subscription_id": sid,
                                    "code": "snapshot_too_large",
                                    "retry_after_ms": None,
                                }
                            )
                            continue
                        snapshot_id = uid()
                        await scoped(
                            sid,
                            {
                                "type": "snapshot.begin",
                                "snapshot_id": snapshot_id,
                                "cursor": sub.cursor,
                                "resource_count": len(resources),
                            },
                        )
                        for index, resource in enumerate(resources):
                            if not await scoped(
                                sid,
                                {
                                    "type": "snapshot.resource",
                                    "snapshot_id": snapshot_id,
                                    "index": index,
                                    "resource": resource,
                                },
                            ):
                                break
                        await scoped(
                            sid,
                            {
                                "type": "snapshot.end",
                                "snapshot_id": snapshot_id,
                                "cursor": sub.cursor,
                                "resource_count": len(resources),
                            },
                        )
                    if sid in subscriptions and await barrier(sid):
                        async with service.ledger.transaction() as tx:
                            _, checkpoint = await sync(service, tx, principal, scope)
                    await tail(sid, checkpoint)
                    await scoped(sid, {"type": "stream.ready", "cursor": sub.cursor})
                    # A fresh subscription may request fresh narration for a committed
                    # result; replay itself never replays speech.
                    if not resume:
                        completed = [
                            obj(obj(r)["value"])
                            for r in resources
                            if obj(r)["kind"] == "action"
                            and obj(obj(r)["value"])["status"] == "succeeded"
                        ]
                        if completed:
                            task = asyncio.create_task(
                                narration(
                                    sid,
                                    completed[-1],
                                    {
                                        "campaign": view.campaign,
                                        "scene": view.scenes[str(scope["scene_id"])],
                                    },
                                )
                            )
                            tasks.add(task)
                            task.add_done_callback(tasks.discard)
            for sid, sub in list(subscriptions.items()):
                if not await barrier(sid):
                    continue
                try:
                    async with service.ledger.transaction() as tx:
                        _, checkpoint = await sync(service, tx, principal, sub.scope)
                except Fault:
                    await barrier(sid)
                    continue
                await tail(sid, checkpoint)
    except ConnectionError:
        await ws.close(code=1013)
    except TimeoutError:
        await ws.close(code=4408)
    except Fault as exc:
        code = (
            exc.code
            if exc.code in ("unauthenticated", "rate_limited", "invalid_cursor")
            else "invalid_message"
        )
        await send(
            {
                "type": "error",
                "request_id": None,
                "subscription_id": None,
                "code": code,
                "retry_after_ms": 1000 if code == "rate_limited" else None,
            }
        )
        await ws.close(
            code=4401 if code == "unauthenticated" else 4429 if code == "rate_limited" else 1002
        )
    except ValueError, TypeError, KeyError:
        await ws.close(code=1002)
    finally:
        subscriptions.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        counters[ip] = max(0, counters.get(ip, 0) - 1)
        if principal_admitted:
            pkey = "principal:" + principal
            counters[pkey] = max(0, counters.get(pkey, 0) - 1)
    return ws
