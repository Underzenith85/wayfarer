"""Recoverable projected history, derived from atomic engine checkpoints.

The folded, audience-declared event stream is the source outbox. Projection aliases
are persisted before delivery; a crash before materialization resumes the scan.
No global revision or source event identity crosses the wire.
"""

from __future__ import annotations

import json
import secrets

from wayfarer.engine.simulation.events import visible

from .common import Fault, Obj, array, encoded, obj, uid
from .ledger import Transaction
from .projection import View
from .service import V1Service


async def sync(service: V1Service, tx: Transaction, principal: str, scope: Obj) -> tuple[View, Obj]:
    cid, aid, sid = str(scope["campaign_id"]), str(scope["actor_id"]), str(scope["scene_id"])
    view = await service.view(tx, cid, principal, aid)
    service.scope(view, aid, sid)
    key = "scope:" + encoded([principal, scope])
    previous = await tx.get(key)
    actions = await service.actions(tx, cid, principal, sid)
    actions = [a for a in actions if a["actor_id"] == aid]
    pending = [a for a in actions if a["status"] not in ("succeeded", "rejected", "cancelled")]
    terminal = [a for a in actions if a not in pending][-100:]
    resources: list[Obj] = [
        {"kind": "campaign", "value": view.campaign},
        {"kind": "scene", "value": view.scenes[sid]},
        {"kind": "character", "value": view.characters[aid]},
        {"kind": "inventory", "value": view.inventories[aid]},
    ]
    resources.extend({"kind": "action", "value": a} for a in pending + terminal)
    if previous is None or previous["policy"] != view.policy:
        previous = {
            "epoch": secrets.token_urlsafe(32),
            "cursor": secrets.token_urlsafe(32),
            "policy": view.policy,
            "revision": view.state.revision,
            "resources": resources,
            "events": [],
            "at": tx.instant.seconds,
        }
    else:
        events = [obj(e) for e in array(previous["events"])]
        old_resources = [obj(r) for r in array(previous["resources"])]
        # Scan all commits after the last pinned boundary, including commits made
        # through other engine adapters. Compare only this authorized projection.
        history = await service.play.store.stream_states(cid, through=view.state.revision)
        candidates: list[list[Obj]] = []
        for state, stream_events in history:
            if not int(str(previous["revision"])) < state["revision"] <= view.state.revision:
                continue
            if not any(visible(event.event, view.member) for event in stream_events):
                continue
            try:
                historical = service.projector.make(
                    state, principal, str(view.campaign["updated_at"]), viewpoint=aid
                )
            except Fault:
                previous["policy"] = ""
                break
            if historical.policy != view.policy:
                previous["policy"] = ""
                break
            candidate_resources: list[Obj] = [
                {"kind": "campaign", "value": historical.campaign},
                {"kind": "scene", "value": historical.scenes[sid]},
                {"kind": "character", "value": historical.characters[aid]},
                {"kind": "inventory", "value": historical.inventories[aid]},
            ]
            candidate_resources.extend(r for r in old_resources if r["kind"] == "action")
            candidates.append(candidate_resources)
        if previous["policy"] != view.policy:
            previous = {
                "epoch": secrets.token_urlsafe(32),
                "cursor": secrets.token_urlsafe(32),
                "policy": view.policy,
                "revision": view.state.revision,
                "resources": resources,
                "events": [],
                "at": tx.instant.seconds,
            }
        else:
            candidates.append(resources)
            for candidate in candidates:
                if encoded(old_resources) == encoded(candidate):
                    continue
                old_map = {
                    (
                        str(obj(r)["kind"]),
                        str(obj(obj(r)["value"]).get("id", obj(obj(r)["value"]).get("actor_id"))),
                    ): obj(obj(r)["value"])
                    for r in old_resources
                }
                changes: list[Obj] = []
                action_updates: list[Obj] = []
                for resource in candidate:
                    kind, value = str(resource["kind"]), obj(resource["value"])
                    rid = str(value.get("id", value.get("actor_id")))
                    if old_map.get((kind, rid)) != value:
                        if kind == "action":
                            record = await tx.get("action:" + rid)
                            history_values = (
                                [obj(h) for h in array(record.get("history", []))] if record else []
                            )
                            prior = old_map.get((kind, rid))
                            start = next(
                                (
                                    i + 1
                                    for i, h in enumerate(history_values)
                                    if prior and h["version"] == prior["version"]
                                ),
                                0,
                            )
                            action_updates.extend(history_values[start:] or [value])
                        else:
                            changes.append(
                                {
                                    "resource_type": kind,
                                    "resource_id": rid,
                                    "version": value["version"],
                                }
                            )
                updates: list[Obj] = []
                if changes:
                    updates.append({"type": "projection.invalidated", "resources": changes})
                updates.extend({"type": "action.updated", "action": a} for a in action_updates)
                for update in updates:
                    cursor = secrets.token_urlsafe(32)
                    events.append(
                        {
                            **update,
                            "event_id": uid(),
                            "previous_cursor": previous["cursor"],
                            "cursor": cursor,
                            "occurred_at": tx.instant.isoformat(),
                            "correlation_id": uid(),
                            "causation_id": None,
                            "_at": tx.instant.seconds,
                        }
                    )
                    previous["cursor"] = cursor
                old_resources = candidate
            previous.update(
                revision=view.state.revision,
                resources=resources,
                events=[e for e in events if float(str(e["_at"])) >= tx.instant.seconds - 1200][
                    -10000:
                ],
            )
    await tx.put(key, previous)
    # Cursor ownership survives eviction, so wrong-scope tokens never become a
    # request for a fresh snapshot. Retention is bounded by connection/replay policy.
    for retained in array(previous["events"]):
        e = obj(retained)
        await tx.put(
            "cursor:" + str(e["cursor"]),
            {"binding": encoded([principal, scope]), "epoch": previous["epoch"], "at": e["_at"]},
        )
    cursor_key = "cursor:" + str(previous["cursor"])
    if await tx.get(cursor_key) is None:
        await tx.put(
            cursor_key,
            {
                "binding": encoded([principal, scope]),
                "epoch": previous["epoch"],
                "at": tx.instant.seconds,
            },
        )
    return view, previous


async def narrate(service: V1Service, principal: str, action: Obj, context: Obj) -> str:
    """Publish only the submitting audience's durable narration result."""
    async with service.ledger.transaction() as tx:
        record = await tx.get("action:" + str(action["id"]))
        if record is None or record["principal"] != principal:
            raise Fault(404, "not_found")
        cid = str(record["campaign"])
        request = obj(record["request"])
        revision = int(
            str(
                record.get(
                    "result_revision", int(str(obj(record["engine"])["expected_revision"])) + 1
                )
            )
        )
    narrator = service.narrate
    if narrator is None:
        raise ValueError("Narration is unavailable")

    async def run() -> str:
        text = await narrator(context, action)
        if not text or len(text) > 4000:
            raise ValueError("Invalid narration")
        return json.dumps({"text": text})

    job = await service.jobs.submit(
        cid=cid,
        revision=revision,
        principal=principal,
        actor=str(request["actor_id"]),
        kind="narration",
        key=str(action["id"]),
        request_json=json.dumps(context),
        run=run,
    )
    await service.jobs.result(job)
    for published in await service.jobs.store.outbox(cid, principal, str(request["actor_id"])):
        if published.id == job.id and published.result_json:
            return str(obj(json.loads(published.result_json))["text"])
    raise ValueError("Narration is not published")
