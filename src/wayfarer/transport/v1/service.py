"""Durable action lifecycle over the existing authoritative transaction engine."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from wayfarer.errors import (
    ConflictError,
    NotFoundError,
    ProviderError,
    ValidationError,
    provider_diagnostic,
)
from wayfarer.models import Campaign
from wayfarer.orchestration.origins import origin_scope
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import SceneService
from wayfarer.persistence.events import CommandOrigin
from wayfarer.simulation.actions import ActionResult

from .common import Fault, Obj, array, encoded, obj, uid, validate
from .ledger import Ledger, Transaction
from .projection import Projector, View


@dataclass(frozen=True)
class Interpretation:
    intent: Obj
    origin: CommandOrigin


Interpreter = Callable[[Obj, str], Awaitable[Obj | Interpretation]]
Narrator = Callable[[Obj, Obj], Awaitable[str]]


class V1Service:
    def __init__(
        self, play: PlayService, path: Path, *, tick_ms: int = 1000, weight_grams: int = 1
    ) -> None:
        self.play, self.ledger = play, Ledger(path)
        self.tick_ms, self.weight_grams = tick_ms, weight_grams
        self.projector: Projector
        self.interpret: Interpreter | None = None
        self.narrate: Narrator | None = None
        self.tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        async with self.ledger.transaction() as tx:
            config = await tx.get("config")
            if config is None:
                config = {"secret": secrets.token_urlsafe(48)}
                await tx.put("config", config)
            self.projector = Projector(
                self.play,
                str(config["secret"]),
                tick_ms=self.tick_ms,
                weight_grams=self.weight_grams,
            )
            pending = [
                str(r["id"])
                for r in await tx.items("action:")
                if obj(r["wire"])["status"] in ("submitted", "resolving")
            ]
        for aid in pending:
            self.schedule(aid)

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def schedule(self, aid: str) -> None:
        task = asyncio.create_task(self.resolve(aid))
        self.tasks.add(task)

        def completed(task: asyncio.Task[None]) -> None:
            self.tasks.discard(task)
            if not task.cancelled():
                task.exception()  # Keep recoverable failures out of unhandled-task logs.

        task.add_done_callback(completed)

    async def view(
        self, tx: Transaction, cid: str, principal: str, viewpoint: str | None = None
    ) -> View:
        try:
            raw = await self.play.store.read(cid)
        except NotFoundError as exc:
            raise Fault(404, "not_found") from exc
        meta = await tx.get("campaign:" + cid)
        if meta is None:
            meta = {"stamp": tx.instant.isoformat()}
            await tx.put("campaign:" + cid, meta)
        self.projector.text_enabled = self.interpret is not None
        return self.projector.make(raw, principal, str(meta["stamp"]), viewpoint=viewpoint)

    @staticmethod
    def scope(view: View, actor: str, scene: str, *, write: bool = False) -> None:
        if actor not in view.characters or view.actor_scenes.get(actor) != scene:
            raise Fault(404, "not_found")
        if write and view.member.role != "player":
            raise Fault(403, "forbidden")

    @staticmethod
    def versions(view: View, request: Obj) -> None:
        actor, scene = str(request["actor_id"]), str(request["scene_id"])
        expected = obj(request["expected_versions"])
        for kind, value in [
            ("scene", view.scenes[scene]),
            ("character", view.characters[actor]),
            ("inventory", view.inventories[actor]),
        ]:
            if kind in expected and expected[kind] != value["version"]:
                raise Fault(409, "stale_version")

    async def action(
        self, tx: Transaction, aid: str, cid: str, principal: str, *, write: bool = False
    ) -> Obj:
        record = await tx.get("action:" + aid)
        if record is None or record["campaign"] != cid:
            raise Fault(404, "not_found")
        wire = obj(record["wire"])
        view = await self.view(tx, cid, principal)
        if write:
            self.scope(view, str(wire["actor_id"]), str(wire["scene_id"]), write=True)
        elif (
            str(wire["actor_id"]) not in view.characters
            or str(record.get("receipt_scene_id", wire["scene_id"])) not in view.scenes
        ):
            raise Fault(404, "not_found")
        if record["principal"] != principal and view.member.role != "gm":
            raise Fault(404, "not_found")
        # A grant/scene reunion does not grant historical action contents.
        if view.member.role == "gm":
            if principal not in array(record.get("gm_readers", [])):
                raise Fault(404, "not_found")
        elif record["policy"] != view.policy:
            raise Fault(404, "not_found")
        return record

    async def recovery_action(self, tx: Transaction, aid: str, cid: str, principal: str) -> Obj:
        record = await tx.get("action:" + aid)
        if record is None:
            raise Fault(404, "not_found")
        if record["engine"] is None:
            return await self.action(tx, aid, cid, principal, write=True)
        view = await self.view(tx, cid, principal)
        if (
            view.member.role != "player"
            or str(obj(record["wire"])["actor_id"]) not in view.member.actor_ids
        ):
            raise Fault(404, "not_found")
        return record

    async def actions(self, tx: Transaction, cid: str, principal: str, scene: str) -> list[Obj]:
        view = await self.view(tx, cid, principal)
        if scene not in view.scenes:
            raise Fault(404, "not_found")
        result: list[Obj] = []
        for record in await tx.items("action:"):
            wire = obj(record["wire"])
            # A turn belongs to the scene it was taken in and to the scene its
            # committed result left the actor standing in. A journey would
            # otherwise vanish from both: filtered out of its destination and
            # unreadable in an origin the actor can no longer see (#294).
            scenes = {str(wire["scene_id"]), str(record.get("receipt_scene_id", wire["scene_id"]))}
            if record["campaign"] == cid and scene in scenes:
                try:
                    await self.action(tx, str(record["id"]), cid, principal)
                except Fault:
                    continue
                result.append(wire)
        # Chronological, with the id only as a tiebreak: a session log is read in
        # the order it happened, not in the order random identifiers sort (#295).
        return sorted(result, key=lambda x: (str(x["created_at"]), str(x["id"])))

    async def submit(self, principal: str, cid: str, path: str, request: Obj) -> Obj:
        validate("SubmitAction", request)
        command_id = str(request["command_id"])
        fingerprint = encoded(["POST", path, request])
        key = "receipt:" + encoded([principal, command_id])
        async with self.ledger.transaction() as tx:
            view = await self.view(tx, cid, principal)
            self.scope(view, str(request["actor_id"]), str(request["scene_id"]), write=True)
            old = await tx.get(key)
            if old is not None:
                if old["fingerprint"] != fingerprint:
                    raise Fault(409, "idempotency_conflict")
                return obj(
                    (await self.action(tx, str(old["action"]), cid, principal, write=True))["wire"]
                )
            if view.state.lifecycle != "active":
                raise Fault(409, "stale_version")
            self.versions(view, request)
            intent = obj(request["intent"])
            if intent["kind"] == "question" or (
                intent["kind"] == "text" and self.interpret is None
            ):
                raise Fault(422, "unsupported_action")
            if intent["kind"] == "use_item":
                items = array(view.inventories[str(request["actor_id"])]["items"])
                if not any(obj(i)["id"] == intent["item_id"] for i in items):
                    raise Fault(404, "not_found")
            if intent["kind"] == "inspect":
                targets = {
                    str(obj(o)["id"])
                    for o in array(view.scenes[str(request["scene_id"])]["observations"])
                }
                targets.update(
                    str(obj(i)["id"])
                    for i in array(view.inventories[str(request["actor_id"])]["items"])
                )
                if str(intent["target_id"]) not in targets:
                    raise Fault(404, "not_found")
            if intent["kind"] == "move":
                self.movement(view, str(request["actor_id"]), str(intent["destination_id"]))
            aid, stamp = uid(), tx.instant.isoformat()
            wire: Obj = {
                "id": aid,
                "command_id": command_id,
                "actor_id": request["actor_id"],
                "scene_id": request["scene_id"],
                "version": uid(),
                "created_at": stamp,
                "updated_at": stamp,
                "status": "submitted",
            }
            record: Obj = {
                "id": aid,
                "campaign": cid,
                "principal": principal,
                "policy": view.policy,
                "request": request,
                "wire": wire,
                "history": [wire],
                "engine": None,
                "gm_readers": [m.principal_id for m in view.state.members if m.role == "gm"],
            }
            await tx.put("action:" + aid, record)
            await tx.put(key, {"fingerprint": fingerprint, "action": aid})
        self.schedule(aid)
        return wire

    def movement(self, view: View, actor_id: str, destination: str) -> Obj:
        rules = view.runtime.engine.rules.scenes
        if rules is not None:
            scene = next(s for s in rules.scenes if s.id == view.actor_scenes[actor_id])
            known = {f for a, f in view.state.world.knowledge if a == actor_id}
            exit = next(
                (
                    e
                    for e in scene.exits
                    if e.destination_id == destination and set(e.required_fact_ids) <= known
                ),
                None,
            )
            if exit is None:
                raise Fault(404, "not_found")
            return {"kind": "travel_scene", "exit_id": exit.id}
        actor = next(e for e in view.state.world.entities if e.id == actor_id)
        if not any(
            c.source_id == actor.location_id and c.destination_id == destination
            for c in view.state.world.connections
        ):
            raise Fault(404, "not_found")
        return {"kind": "move", "destination_id": destination}

    async def resolve(self, aid: str) -> None:
        # Interpretation is advisory and happens outside every database transaction.
        committed = False
        try:
            async with self.ledger.transaction() as tx:
                record = await tx.get("action:" + aid)
                if record is None or obj(record["wire"])["status"] not in (
                    "submitted",
                    "resolving",
                ):
                    return
                principal, cid = str(record["principal"]), str(record["campaign"])
                request = obj(record["request"])
                generation = obj(record["wire"])["version"]
                view = await self.view(tx, cid, principal)
                context: Obj = {
                    "campaign": view.campaign,
                    "scene": view.scenes[str(request["scene_id"])],
                    "character": view.characters[str(request["actor_id"])],
                    "inventory": view.inventories[str(request["actor_id"])],
                }
                intent = obj(request["intent"])
            origin = None
            if record["engine"] is None and intent["kind"] in ("text", "question"):
                if self.interpret is None:
                    raise Fault(422, "unsupported_action")
                async with asyncio.timeout(20):
                    interpreted = await self.interpret(context, str(intent["text"]))
                    if isinstance(interpreted, Interpretation):
                        intent, origin = interpreted.intent, interpreted.origin
                    else:
                        intent = interpreted
                if "clarification" in intent:
                    clarification = validate("Clarification", intent["clarification"])
                    async with self.ledger.transaction() as tx:
                        current = await self.action(tx, aid, cid, principal, write=True)
                        if (
                            obj(current["wire"])["status"] != "submitted"
                            or obj(current["wire"])["version"] != generation
                        ):
                            return
                        self.transition(
                            current,
                            "needs_clarification",
                            clarification=clarification,
                            at=tx.instant.isoformat(),
                        )
                        await tx.put("action:" + aid, current)
                    return
                validate("Intent", intent)
                if intent["kind"] in ("text", "question"):
                    raise Fault(422, "unsupported_action")
            # Save the exact engine attempt before dispatch. A crash between engine
            # commit and receipt finalization reuses this payload and engine receipt.
            async with self.ledger.transaction() as tx:
                record = await self.recovery_action(tx, aid, cid, principal)
                if obj(record["wire"])["status"] not in ("submitted", "resolving"):
                    return
                if record["engine"] is None:
                    if obj(record["wire"])["version"] != generation:
                        return
                    view = await self.view(tx, cid, principal)
                    self.versions(view, obj(record["request"]))
                    if intent["kind"] == "move":
                        intent = self.movement(
                            view, str(request["actor_id"]), str(intent["destination_id"])
                        )
                    record["origin"] = origin.model_dump(mode="json") if origin else None
                    record["engine"] = {
                        **intent,
                        "id": aid,
                        "actor_id": request["actor_id"],
                        "expected_revision": view.state.revision,
                    }
                self.transition(record, "resolving", at=tx.instant.isoformat())
                await tx.put("action:" + aid, record)
            async with self.ledger.transaction() as tx:
                record = await self.recovery_action(tx, aid, cid, principal)
                if obj(record["wire"])["status"] != "resolving":
                    return
                command = obj(record["engine"])
                meta = await tx.get("campaign:" + cid)
                assert meta is not None

                def authorize(raw: Campaign) -> None:
                    current = self.projector.make(raw, principal, str(meta["stamp"]))
                    self.scope(
                        current, str(request["actor_id"]), str(request["scene_id"]), write=True
                    )
                    if current.state.lifecycle != "active":
                        raise Fault(409, "stale_version")
                    self.versions(current, obj(record["request"]))

                # Hidden-only revision races may be retried under the same visible
                # versions; never overwrite the saved attempt after an uncertain commit.
                origin = (
                    CommandOrigin.model_validate(record["origin"]) if record.get("origin") else None
                )
                try:
                    with origin_scope(origin):
                        if command["kind"] == "travel_scene":
                            event = await SceneService(view.runtime).execute(
                                cid,
                                command,
                                authenticated_actor_id=str(request["actor_id"]),
                                authorize=authorize,
                            )
                            result = ActionResult(
                                status="committed",
                                revision=event.revision,
                                code="scene.travelled",
                                command_id=aid,
                            )
                        else:
                            result = await view.runtime.execute(
                                cid,
                                command,
                                authenticated_actor_id=str(request["actor_id"]),
                                authorize=authorize,
                            )
                except ConflictError:
                    current_view = await self.view(tx, cid, principal)
                    self.versions(current_view, obj(record["request"]))
                    attempts = int(str(record.get("attempts", 0))) + 1
                    if attempts > 3 or current_view.state.revision == command["expected_revision"]:
                        raise Fault(409, "stale_version") from None
                    record["attempts"] = attempts
                    command = {**command, "expected_revision": current_view.state.revision}
                    record["engine"] = command
                    await tx.put("action:" + aid, record)
                    # Persist the replacement attempt before a future dispatch.
                    self.transition(record, "submitted", at=tx.instant.isoformat())
                    await tx.put("action:" + aid, record)
                    self.schedule(aid)
                    return
                if result.status != "committed":
                    code = (
                        "unsupported_action"
                        if result.status in ("unsupported", "question", "adjudication_required")
                        else "illegal_action"
                    )
                    raise Fault(422, code)
                committed = True
                after = await self.view(tx, cid, principal)
                checks: list[Obj] = []
                if result.check is not None:
                    c = result.check
                    checks.append(
                        {
                            "label": "Authoritative check",
                            "dice": list(c.dice),
                            "target": c.effective_target,
                            "margin": c.margin,
                            "outcome": c.outcome.value.replace("-", "_"),
                        }
                    )
                changed = self.resources(after, str(request["actor_id"]), str(request["scene_id"]))
                self.transition(
                    record,
                    "succeeded",
                    resolution={
                        "summary": "Action resolved by the engine.",
                        "checks": checks,
                        "changed_resources": changed,
                        "game_time": after.campaign["game_time"],
                    },
                    at=tx.instant.isoformat(),
                )
                # Knowledge-changing actions remain readable to their submitting
                # principal under the new view, but no other principal inherits them.
                record["policy"] = after.policy
                record["receipt_scene_id"] = after.actor_scenes[str(request["actor_id"])]
                await tx.put("action:" + aid, record)
        except asyncio.CancelledError:
            raise
        except (Fault, ValidationError, NotFoundError, TimeoutError, ProviderError) as exc:
            if committed:
                return  # Leave the saved attempt recoverable; never reject an engine commit.
            fault = (
                exc
                if isinstance(exc, Fault)
                else Fault(503, "service_unavailable")
                if isinstance(exc, (TimeoutError, ProviderError))
                else Fault(422, "illegal_action")
            )
            async with self.ledger.transaction() as tx:
                failed = await tx.get("action:" + aid)
                if failed and obj(failed["wire"])["status"] not in ("succeeded", "cancelled"):
                    error = fault.wire(uid())
                    if isinstance(exc, ProviderError):
                        diagnostic = provider_diagnostic(exc)
                        error.update(message=diagnostic.message, retryable=diagnostic.retryable)
                    self.transition(failed, "rejected", error=error, at=tx.instant.isoformat())
                    await tx.put("action:" + aid, failed)

    @staticmethod
    def transition(record: Obj, status: str, *, at: str, **extra: object) -> None:
        old = obj(record["wire"])
        wire = {k: v for k, v in old.items() if k not in ("resolution", "error", "clarification")}
        wire.update(status=status, version=uid(), updated_at=at, **extra)
        record["wire"] = validate("Action", wire)
        record["history"] = [*array(record.get("history", [])), wire][-10000:]

    @staticmethod
    def resources(view: View, actor: str, scene: str) -> list[Obj]:
        values = [
            ("campaign", view.campaign, str(view.campaign["id"])),
            ("character", view.characters[actor], actor),
            ("inventory", view.inventories[actor], actor),
        ]
        scene = view.actor_scenes.get(actor, scene)
        if scene in view.scenes:
            values.append(("scene", view.scenes[scene], scene))
        return [
            {"resource_type": kind, "resource_id": key, "version": value["version"]}
            for kind, value, key in values
        ]

    async def control(
        self, principal: str, cid: str, aid: str, path: str, request: Obj, *, cancel: bool
    ) -> Obj:
        validate("CancelAction" if cancel else "ClarifyAction", request)
        key = "receipt:" + encoded([principal, request["command_id"]])
        fingerprint = encoded(["POST", path, request])
        async with self.ledger.transaction() as tx:
            record = await self.action(tx, aid, cid, principal, write=True)
            old = await tx.get(key)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Fault(409, "idempotency_conflict")
                return obj(record["wire"])
            wire = obj(record["wire"])
            if wire["version"] != request["expected_action_version"]:
                raise Fault(409, "stale_version")
            if wire["status"] not in (
                ("submitted", "needs_clarification") if cancel else ("needs_clarification",)
            ):
                raise Fault(409, "invalid_transition")
            if cancel:
                self.transition(record, "cancelled", at=tx.instant.isoformat())
            else:
                clarification = obj(wire["clarification"])
                answer = obj(request["answer"])
                if request["clarification_id"] != clarification["id"]:
                    raise Fault(409, "invalid_transition")
                text = answer.get("text")
                if text is not None and not clarification["allows_text"]:
                    raise Fault(400, "invalid_request")
                if "choice_id" in answer:
                    choice = next(
                        (
                            obj(c)
                            for c in array(clarification["choices"])
                            if obj(c)["id"] == answer["choice_id"]
                        ),
                        None,
                    )
                    if choice is None:
                        raise Fault(400, "invalid_request")
                    text = choice["label"]
                original = obj(record["request"])
                record["request"] = {
                    **original,
                    "expected_versions": request["expected_versions"],
                    "intent": {"kind": "text", "text": text},
                }
                view = await self.view(tx, cid, principal)
                self.versions(view, obj(record["request"]))
                record["engine"] = None
                self.transition(record, "submitted", at=tx.instant.isoformat())
            await tx.put("action:" + aid, record)
            await tx.put(key, {"fingerprint": fingerprint, "action": aid})
        if not cancel:
            self.schedule(aid)
        return obj(record["wire"])
