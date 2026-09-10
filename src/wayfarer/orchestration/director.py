"""Resumable interpretation → domain transaction → narration.

Domain services atomically order resolution, time, NPC reactions and objectives.
The director never repeats that cascade: it recovers the command's stored receipt.
Provider calls happen outside transactions, and checkpoint CAS discards late replies.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping

from wayfarer.errors import AuthorizationError, ConflictError, ProviderError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.providers import (
    Intent,
    Narration,
    Orchestrator,
    ProviderRequest,
    TurnResponse,
)
from wayfarer.simulation.director import DirectorTurn


class DirectorService:
    def __init__(self, orchestrator: Orchestrator) -> None:
        self.llm = orchestrator
        self.access = orchestrator.access
        self.play = self.access.play

    async def _save(self, cid: str, turn: DirectorTurn, revision: int) -> None:
        payload = turn.model_dump_json()
        key = "director:" + hashlib.sha256(payload.encode()).hexdigest()[:64]

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            state = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "resources": state.resources.model_copy(
                        update={"revision": state.revision + 1}
                    ),
                    "rulings": tuple(
                        r.model_copy(update={"valid_revision": state.revision + 1})
                        if r.current_status(state.revision, state.resources.game_time)
                        in ("pending", "approved")
                        else r
                        for r in state.rulings
                    ),
                    "director": tuple(t for t in state.director if t.id != turn.id) + (turn,),
                }
            )
            self.play.commit(campaign, state)
            return Event(input=payload, action="director", outcome=turn.phase, roll=None)

        await self.play.store.commit_turn(
            cid, key, revision, payload, resolve, actor_id=turn.actor_id
        )

    async def run(
        self,
        cid: str,
        *,
        principal_id: str,
        actor_id: str,
        command_id: str,
        text: str,
        checkpoint: Callable[[str], None] | None = None,
        proposal: Mapping[str, object] | None = None,
    ) -> TurnResponse:
        request_json = json.dumps(dict(proposal), sort_keys=True) if proposal is not None else None
        # A bounded phase loop; crashes can be injected after each durable boundary.
        for _ in range(8):
            state = self.play._load(await self.play.store.read(cid))
            member = self.access._member(state, principal_id)
            self.access._control(member, actor_id)
            turn = next((t for t in state.director if t.id == command_id), None)
            if turn is not None and (turn.actor_id, turn.principal_id, turn.text) != (
                actor_id,
                principal_id,
                text,
            ):
                raise ConflictError("Turn identity was already used for different input")
            if turn is not None and request_json is not None and turn.request_json != request_json:
                raise ConflictError("Turn request payload changed")
            if state.lifecycle != "active" and (turn is None or turn.phase != "complete"):
                raise ConflictError("Resume an active campaign before acting")
            if turn is None:
                if (
                    proposal is not None
                    and "expected_revision" in proposal
                    and proposal["expected_revision"] != state.revision
                ):
                    raise ConflictError("Typed action context changed")
                if any(
                    t.actor_id == actor_id and t.phase not in ("complete", "clarification")
                    for t in state.director
                ):
                    raise ConflictError("Resume the pending turn before starting another")
                _, session, _ = await self.llm.context(cid, principal_id, actor_id)
                turn = DirectorTurn(
                    id=command_id,
                    actor_id=actor_id,
                    principal_id=principal_id,
                    session_id=session,
                    text=text,
                    request_json=request_json,
                    command_json=json.dumps(
                        {
                            **proposal,
                            "id": "turn:" + hashlib.sha256(command_id.encode()).hexdigest()[:64],
                            "actor_id": actor_id,
                            "expected_revision": state.revision + 1,
                        }
                    )
                    if proposal is not None
                    else None,
                    phase="resolution" if proposal is not None else "interpretation",
                )
                if (
                    proposal is not None
                    and turn.command_json is not None
                    and len(state.party.groups) > 1
                    and proposal.get("kind")
                    in (
                        "wait",
                        "inspect",
                        "social",
                        "use_item",
                        "travel_scene",
                        "approach_noncombat",
                    )
                ):
                    from wayfarer.orchestration.party import PartyCommand

                    queued_command = json.loads(turn.command_json)
                    turn = turn.model_copy(
                        update={
                            "command_json": PartyCommand(
                                id=queued_command["id"],
                                actor_id=actor_id,
                                expected_revision=state.revision + 1,
                                kind="queue_activity",
                                activity_json=turn.command_json,
                            ).model_dump_json()
                        }
                    )
                await self._save(cid, turn, state.revision)
                if checkpoint:
                    checkpoint("interpretation")
                continue
            if turn.phase in ("complete", "clarification"):
                return TurnResponse(
                    committed=turn.committed,
                    projection=await self.access.read(cid, principal_id=principal_id),
                    narration=turn.narration,
                    narration_available=turn.narration_available,
                )
            if turn.phase == "interpretation":
                context, session, revision = await self.llm.context(cid, principal_id, actor_id)
                if session != turn.session_id:
                    turn = turn.model_copy(
                        update={
                            "phase": "clarification",
                            "narration": "Your subgroup changed. Submit a fresh action.",
                        }
                    )
                    await self._save(cid, turn, revision)
                    continue
                try:
                    intent = Intent.model_validate_json(
                        await self.llm._call(
                            ProviderRequest(
                                operation="intent",
                                session_id=session,
                                context_json=context,
                                prompt=text,
                                output_schema=Intent.model_json_schema(),
                            )
                        )
                    )
                    command = intent.command(
                        "turn:" + hashlib.sha256(command_id.encode()).hexdigest()[:64],
                        actor_id,
                        revision + 1,
                    )
                except ValueError as exc:
                    raise ProviderError("Invalid structured intent") from exc
                # The saved interpretation consumes one revision. Domain command starts next.
                current_context = await self.llm.context(cid, principal_id, actor_id)
                if current_context[1:] != (session, revision):
                    raise ConflictError("Interpretation context changed")
                if len(state.party.groups) > 1 and intent.kind in (
                    "inspect",
                    "social",
                    "use_item",
                    "wait",
                    "travel_scene",
                    "approach_noncombat",
                ):
                    from wayfarer.orchestration.party import PartyCommand

                    command = PartyCommand(
                        id=str(command["id"]),
                        actor_id=actor_id,
                        expected_revision=revision + 1,
                        kind="queue_activity",
                        activity_json=json.dumps(command),
                    ).model_dump(mode="json")
                turn = turn.model_copy(
                    update={"command_json": json.dumps(command), "phase": "resolution"}
                )
                await self._save(cid, turn, revision)
                if checkpoint:
                    checkpoint("resolution")
                continue
            if turn.phase == "resolution":
                if turn.command_json is None:
                    raise ValidationError("Missing persisted interpretation")
                command = json.loads(turn.command_json)
                history = await self.play.store.history(cid)
                receipt = next(
                    (
                        e
                        for e in history
                        if e.command_id == command["id"] and e.actor_id == actor_id
                    ),
                    None,
                )
                if receipt is None:
                    if command["expected_revision"] != state.revision:
                        # Never silently rebase a model decision across changed world state.
                        turn = turn.model_copy(
                            update={
                                "phase": "clarification",
                                "narration": "The world changed before resolution. Submit a fresh action.",
                            }
                        )
                        await self._save(cid, turn, state.revision)
                        continue
                    try:
                        await self.access.execute(cid, command, principal_id=principal_id)
                    except (ValidationError, ConflictError, AuthorizationError) as exc:
                        current = self.play._load(await self.play.store.read(cid))
                        turn = turn.model_copy(
                            update={"phase": "clarification", "narration": str(exc)}
                        )
                        await self._save(cid, turn, current.revision)
                        continue
                    if checkpoint:
                        checkpoint("domain_committed")
                    history = await self.play.store.history(cid)
                    receipt = next(
                        (
                            e
                            for e in history
                            if e.command_id == command["id"] and e.actor_id == actor_id
                        ),
                        None,
                    )
                state = self.play._load(await self.play.store.read(cid))
                if receipt is None:
                    result = await self.play.preview(cid, command, authenticated_actor_id=actor_id)
                    turn = turn.model_copy(
                        update={
                            "phase": "narration"
                            if result.status == "question"
                            else "clarification",
                            "narration": result.code,
                            "outcome_json": result.model_dump_json(),
                        }
                    )
                else:
                    # Only metadata enters narration; hidden raw event payloads are excluded.
                    queued = any(q.id == command["id"] for q in state.party.queue)
                    turn = turn.model_copy(
                        update={
                            "phase": "waiting" if queued else "narration",
                            "committed": not queued,
                            "narration": "Waiting for shared-time coordination." if queued else "",
                            "trace_json": receipt.event["outcome"]
                            if receipt.event["action"] in ("typed-action", "combat", "scene")
                            else None,
                            "outcome_json": json.dumps(
                                {
                                    "revision": receipt.resulting_revision,
                                    "action": receipt.event["action"],
                                }
                            ),
                        }
                    )
                await self._save(cid, turn, state.revision)
                if checkpoint:
                    checkpoint(turn.phase)
                continue
            if turn.phase == "waiting" or (
                turn.phase == "narration" and turn.command_json is not None
            ):
                # A queue receipt commits scheduling, not the eventual action. Only
                # the scheduler's durable outcome permits success narration.
                command = json.loads(turn.command_json or "{}")
                activity = next((q for q in state.party.queue if q.id == command.get("id")), None)
                settled = next((r for r in state.party.receipts if r.id == command.get("id")), None)
                if activity is not None:
                    if turn.phase != "waiting":
                        turn = turn.model_copy(
                            update={
                                "phase": "waiting",
                                "committed": False,
                                "narration": "Waiting for shared-time coordination.",
                                "narration_available": False,
                            }
                        )
                        await self._save(cid, turn, state.revision)
                        if checkpoint:
                            checkpoint("waiting")
                    return TurnResponse(
                        committed=False,
                        projection=await self.access.read(cid, principal_id=principal_id),
                        narration=turn.narration,
                        narration_available=False,
                    )
                if settled is not None:
                    turn = turn.model_copy(
                        update={
                            "phase": "narration"
                            if settled.status == "committed"
                            else "clarification",
                            "committed": settled.status == "committed",
                            "outcome_json": settled.model_dump_json(),
                            "narration": "" if settled.status == "committed" else settled.code,
                        }
                    )
                    if turn.phase == "clarification":
                        await self._save(cid, turn, state.revision)
                        continue
                elif turn.phase == "waiting":
                    raise ValidationError("Missing scheduled activity outcome")
                recovery = next(
                    (d for d in state.recovery.decisions if d.id == command.get("id")), None
                )
                if recovery is not None:
                    # A resolved check may fail, and scheduling a recovery option
                    # may require adjudication. Preserve that distinction in prose.
                    turn = turn.model_copy(
                        update={
                            "outcome_json": recovery.model_dump_json(
                                include={"status", "option_id", "due"}
                            )
                        }
                    )
                    if recovery.status in ("rejected", "adjudication_required"):
                        turn = turn.model_copy(
                            update={
                                "phase": "clarification",
                                "committed": False,
                                "narration": f"Recovery {recovery.status}. Submit a fresh choice.",
                            }
                        )
                        await self._save(cid, turn, state.revision)
                        continue
            context, session, revision = await self.llm.context(cid, principal_id, actor_id)
            narration, available = (
                (
                    "Action committed. The current state is available."
                    if turn.committed
                    else "No game state changed. The current visible state is available."
                ),
                False,
            )
            if session == turn.session_id:
                try:
                    narration = Narration.model_validate_json(
                        await self.llm._call(
                            ProviderRequest(
                                operation="narration",
                                session_id=session,
                                context_json=json.dumps(
                                    {
                                        "visible_state": json.loads(context),
                                        "committed": json.loads(turn.outcome_json or "{}"),
                                    }
                                ),
                                prompt="Describe only the committed outcome and visible facts."
                                if turn.committed
                                else f"Answer using only visible facts, without proposing mutations: {turn.text}",
                                output_schema=Narration.model_json_schema(),
                            )
                        )
                    ).text
                    available = True
                except ProviderError, ValueError:
                    pass
            turn = turn.model_copy(
                update={
                    "phase": "complete",
                    "narration": narration,
                    "narration_available": available,
                }
            )
            await self._save(cid, turn, revision)
            if checkpoint:
                checkpoint("complete")
        raise ConflictError("Director phase budget exhausted; resume this turn")
