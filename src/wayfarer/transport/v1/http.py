"""Frozen route table, authentication, HTTP errors and bounded pagination."""

from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from pathlib import Path

from aiohttp import web

from wayfarer.errors import WayfarerError
from wayfarer.orchestration.play import PlayService

from .common import OPENAPI, Fault, Obj, array, encoded, obj, uid, validate
from .service import V1Service

SERVICE = web.AppKey("v1-service", V1Service)
TOKENS = web.AppKey("v1-tokens", dict[str, str])
ORIGINS = web.AppKey("v1-origins", frozenset[str])
NO_ORIGIN = web.AppKey("v1-no-origin", bool)
LIMITS = web.AppKey("v1-limits", dict[str, tuple[float, int]])
IDENTITY = web.RequestKey("v1-identity", str)
REQUEST_ID = web.RequestKey("v1-request-id", str)


def identity(tokens: dict[str, str], credential: str) -> str:
    for key, principal in tokens.items():
        if hmac.compare_digest(key, credential):
            return principal
    raise Fault(401, "unauthenticated")


def response(value: Obj, request_id: str, status: int = 200) -> web.Response:
    headers = {"Cache-Control": "no-store", "X-Request-ID": request_id}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status in (429, 503):
        headers["Retry-After"] = "1"
    return web.json_response(value, status=status, headers=headers)


@web.middleware
async def boundary(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    if not request.path.startswith("/api/v1"):
        return await handler(request)
    request_id = uid()
    request[REQUEST_ID] = request_id
    try:
        if not request.secure and not ipaddress.ip_address(request.remote or "0.0.0.0").is_loopback:
            raise Fault(403, "forbidden")
        if request.path != "/api/v1/live":
            credential = request.headers.get("Authorization", "")
            if not credential.startswith("Bearer "):
                raise Fault(401, "unauthenticated")
            principal = identity(request.app[TOKENS], credential[7:])
            request[IDENTITY] = principal
            now = time.monotonic()
            start, count = request.app[LIMITS].get(principal, (now, 0))
            if now - start >= 60:
                start, count = now, 0
            if count >= 120:
                raise Fault(429, "rate_limited")
            request.app[LIMITS][principal] = (start, count + 1)
        return await handler(request)
    except Fault as exc:
        if exc.status == 404 and request.query.get("cursor"):
            async with request.app[SERVICE].ledger.transaction() as tx:
                cursor = await tx.get("page:" + request.query["cursor"])
            if cursor and cursor["binding"] == encoded(
                [request.get(IDENTITY), request.path, request.query.get("scene_id")]
            ):
                exc = Fault(410, "cursor_expired")
        return response(exc.wire(request_id), request_id, exc.status)
    except web.HTTPRequestEntityTooLarge:
        return response(Fault(413, "payload_too_large").wire(request_id), request_id, 413)
    except web.HTTPException as exc:
        code = "not_found" if exc.status == 404 else "invalid_request"
        status = 404 if exc.status == 404 else 400
        return response(Fault(status, code).wire(request_id), request_id, status)
    except ValueError, TypeError, KeyError:
        return response(Fault(400, "invalid_request").wire(request_id), request_id, 400)
    except WayfarerError:
        return response(Fault(503, "service_unavailable").wire(request_id), request_id, 503)
    except Exception:
        # Never serialize internal exception strings or engine/provider context.
        return response(Fault(500, "internal_error").wire(request_id), request_id, 500)


async def body(request: web.Request, schema: str) -> Obj:
    if request.content_type != "application/json":
        raise Fault(415, "unsupported_media_type")
    chunks = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        chunks.extend(chunk)
        if len(chunks) > 32000:
            raise Fault(413, "payload_too_large")
    return validate(schema, json.loads(chunks))


async def page(request: web.Request, values: list[Obj], policy: str) -> Obj:
    service = request.app[SERVICE]
    principal = request[IDENTITY]
    query = dict(request.query)
    if set(query) - {"limit", "cursor", "scene_id"} or len(request.query) != len(query):
        raise Fault(400, "invalid_request")
    limit = int(query.get("limit", "50"))
    if not 1 <= limit <= 100:
        raise Fault(400, "invalid_request")
    binding = encoded([principal, request.path, query.get("scene_id")])
    supplied = query.get("cursor")
    async with service.ledger.transaction() as tx:
        start = 0
        expires = time.time() + 900
        if supplied:
            cursor = await tx.get("page:" + supplied)
            if not cursor or cursor["binding"] != binding:
                raise Fault(400, "invalid_cursor")
            if float(str(cursor["expires"])) < time.time() or cursor["policy"] != policy:
                raise Fault(410, "cursor_expired")
            values = [obj(v) for v in array(cursor["items"])]
            start = int(str(cursor["start"]))
            expires = float(str(cursor["expires"]))
        # A total order the cursor can resume from: chronological where the
        # resource records when it was created, and by identifier otherwise,
        # so a page boundary never reorders a session log (#295).
        ordered = sorted(
            values,
            key=lambda x: (
                str(x.get("created_at", "")),
                str(x.get("id", x.get("principal_id"))),
            ),
        )
        selected = ordered[start : start + limit]
        token: str | None = None
        if start + limit < len(ordered):
            token = secrets.token_urlsafe(32)
            await tx.put(
                "page:" + token,
                {
                    "binding": binding,
                    "policy": policy,
                    "expires": expires,
                    "items": ordered,
                    "start": start + limit,
                },
            )
        return {"items": selected, "next_cursor": token}


async def route(
    request: web.Request,
    *,
    operation: str,
    request_schema: str | None,
    replies: dict[str, str],
    attempt: int = 0,
) -> web.Response:
    service = request.app[SERVICE]
    principal, request_id = request[IDENTITY], request[REQUEST_ID]
    for value in request.match_info.values():
        from jsonschema import Draft202012Validator

        from .common import HTTP, REGISTRY

        if not Draft202012Validator(
            {"$ref": f"{HTTP['$id']}#/$defs/Id"}, registry=REGISTRY
        ).is_valid(value):
            raise Fault(400, "invalid_request")
    if request_schema:
        data = await body(request, request_schema)
    else:
        data = {}
    cid = request.match_info.get("campaign_id", "")
    aid = request.match_info.get("action_id", "")
    allowed_query = {"limit", "cursor"} if operation.startswith("list") else set()
    if operation == "listActions":
        allowed_query.add("scene_id")
    if set(request.query) - allowed_query or len(request.query) != len(dict(request.query)):
        raise Fault(400, "invalid_request")
    status = 200
    policy: str | None = None
    result: Obj
    if operation == "submitAction":
        result = await service.submit(principal, cid, request.path, data)
        status = 202
    elif operation in ("clarifyAction", "cancelAction"):
        result = await service.control(
            principal, cid, aid, request.path, data, cancel=operation == "cancelAction"
        )
        status = 200 if operation == "cancelAction" else 202
    elif operation in ("createInvitation", "redeemInvitation"):
        from .invitations import invitation

        result = await invitation(
            service, principal, cid, request.path, data, redeem=operation == "redeemInvitation"
        )
        status = 200 if operation == "redeemInvitation" else 201
    elif operation == "getMe":
        result = {"id": principal, "display_name": principal}
    else:
        values: list[Obj] | None = None
        async with service.ledger.transaction() as tx:
            if operation == "listCampaigns":
                values = []
                policies: list[str] = []
                for campaign in await service.play.store.listing():
                    try:
                        view = await service.view(tx, campaign["id"], principal)
                    except Fault as exc:
                        if exc.status == 404:
                            continue
                        raise
                    values.append(view.campaign)
                    policies.append(view.policy)
                policy = encoded(policies)
            else:
                view = await service.view(tx, cid, principal)
                policy = view.policy
                if operation == "getCampaign":
                    result = view.campaign
                elif operation == "getCurrentSession":
                    # Engine session lifecycle has no persisted wall-clock session yet.
                    raise Fault(404, "not_found")
                elif operation == "listMembers":
                    visible = {
                        str(a) for s in view.scenes.values() for a in array(s["visible_actor_ids"])
                    }
                    values = [
                        service.projector.versioned(
                            principal,
                            cid,
                            "member",
                            {
                                "principal_id": m.principal_id,
                                "role": m.role,
                                "campaign_id": cid,
                                "actor_ids": [
                                    a
                                    for a in m.actor_ids
                                    if a in visible
                                    or m.principal_id == principal
                                    or view.member.role == "gm"
                                ],
                            },
                        )
                        for m in view.state.members
                    ]
                elif operation == "listCharacters":
                    values = list(view.characters.values())
                elif operation == "listScenes":
                    values = list(view.scenes.values())
                elif operation in ("getCharacter", "getInventory", "getScene"):
                    key = request.match_info.get("actor_id", request.match_info.get("scene_id", ""))
                    source = (
                        view.characters
                        if operation == "getCharacter"
                        else view.inventories
                        if operation == "getInventory"
                        else view.scenes
                    )
                    if key not in source:
                        raise Fault(404, "not_found")
                    result = source[key]
                elif operation == "getAction":
                    result = obj((await service.action(tx, aid, cid, principal))["wire"])
                elif operation == "listActions":
                    values = await service.actions(
                        tx, cid, principal, request.query.get("scene_id", "")
                    )
                else:
                    raise Fault(404, "not_found")
        if values is not None:
            result = await page(request, values, policy or "")
    if identity(request.app[TOKENS], request.headers["Authorization"][7:]) != principal:
        raise Fault(401, "unauthenticated")
    retry_read = False
    if cid:
        async with service.ledger.transaction() as tx:
            final_view = await service.view(tx, cid, principal)
            if policy is not None and final_view.policy != policy:
                if request.query.get("cursor"):
                    raise Fault(410, "cursor_expired")
                retry_read = True
    elif operation == "listCampaigns":
        permitted: list[Obj] = []
        async with service.ledger.transaction() as tx:
            for payload in array(result["items"]):
                item = obj(payload)
                try:
                    await service.view(tx, str(item["id"]), principal)
                except Fault:
                    if request.query.get("cursor"):
                        raise Fault(410, "cursor_expired") from None
                    continue
                permitted.append(item)
        result["items"] = permitted
    if retry_read:
        if attempt >= 2:
            raise Fault(503, "service_unavailable")
        return await route(
            request,
            operation=operation,
            request_schema=request_schema,
            replies=replies,
            attempt=attempt + 1,
        )
    validate(replies[str(status)], result)
    return response(result, request_id, status)


def install(
    app: web.Application,
    play: PlayService,
    tokens: Mapping[str, str],
    path: Path,
    *,
    origins: frozenset[str] = frozenset(),
    allow_no_origin: bool = False,
) -> V1Service:
    service = V1Service(play, path)
    app[SERVICE], app[TOKENS] = service, dict(tokens)
    app[ORIGINS], app[NO_ORIGIN], app[LIMITS] = origins, allow_no_origin, {}
    app.middlewares.insert(0, boundary)
    for path_template, methods in obj(OPENAPI["paths"]).items():
        for method, spec in obj(methods).items():
            operation = obj(spec)
            if "operationId" not in operation:
                continue
            request_schema = None
            if "requestBody" in operation:
                request_schema = str(
                    obj(
                        obj(obj(obj(operation["requestBody"])["content"])["application/json"])[
                            "schema"
                        ]
                    )["$ref"]
                ).split("/")[-1]
            replies: dict[str, str] = {}
            for status, raw in obj(operation["responses"]).items():
                reply = obj(raw)
                if "$ref" in reply:
                    reply = obj(
                        obj(obj(OPENAPI["components"])["responses"])[
                            str(reply["$ref"]).split("/")[-1]
                        ]
                    )
                replies[status] = str(
                    obj(obj(obj(reply["content"])["application/json"])["schema"])["$ref"]
                ).split("/")[-1]
            app.router.add_route(
                method.upper(),
                "/api/v1" + path_template,
                partial(
                    route,
                    operation=str(operation["operationId"]),
                    request_schema=request_schema,
                    replies=replies,
                ),
            )
    from .live import live

    app.router.add_get("/api/v1/live", live)
    return service
