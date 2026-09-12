"""Transactional ruling workflow, with policy-owned automatic decisions.

Transport must supply authenticated identities independently of command JSON.
No provider call is made here or within a database transaction.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.engine.simulation.actions import (
    ACTION_ADAPTER,
    ActionCommand,
    ActionResult,
    PlayState,
    Social,
)
from wayfarer.engine.simulation.adjudication import Ruling, expire_rulings
from wayfarer.engine.simulation.events import action_result
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt, Id
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class RequestRuling(ActionCommand):
    kind: Literal["request_ruling"] = "request_ruling"
    action: Social


class DecideRuling(ActionCommand):
    kind: Literal["decide_ruling"] = "decide_ruling"
    ruling_id: Id
    approve: bool
    alternative_id: Id | None = None
    reason: str = Field(min_length=1, max_length=2000)


class ExecuteRuling(ActionCommand):
    kind: Literal["execute_ruling"] = "execute_ruling"
    ruling_id: Id


class EvaluateRuling(ActionCommand):
    """Internal policy evaluator command, deliberately absent from the input union."""

    kind: Literal["evaluate_ruling"] = "evaluate_ruling"
    ruling_id: Id


RulingCommand = Annotated[RequestRuling | DecideRuling | ExecuteRuling, Field(discriminator="kind")]
RULING_ADAPTER: TypeAdapter[RulingCommand] = TypeAdapter(RulingCommand)


class AdjudicationService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def submit(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> Ruling | ActionResult:
        try:
            command = RULING_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid ruling command") from exc
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Ruling command actor is not authorized")
        return await self._commit(cid, command)

    async def evaluate(
        self, cid: str, ruling_id: str, *, command_id: str, expected_revision: int
    ) -> Ruling:
        """Trusted scheduling hook; policy, not an LLM, selects and approves.

        The first authored alternative inside the automatic bounds wins. If none
        qualifies, do not write a decision or consume a revision. Human approval
        remains available through submit(). Do not expose this as a model tool.
        """
        result = await self._commit(
            cid,
            EvaluateRuling(
                id=command_id,
                actor_id="system:adjudication",
                expected_revision=expected_revision,
                ruling_id=ruling_id,
            ),
        )
        if not isinstance(result, Ruling):
            raise ValidationError("Missing policy decision")
        return result

    def _request(self, state: PlayState, command: RequestRuling) -> PlayState:
        policy = self.play.engine.rules.adjudication
        if policy is None:
            raise ValidationError("Campaign adjudication is not configured")
        action = command.action
        if (
            action.actor_id != command.actor_id
            or action.expected_revision != command.expected_revision
            or action.hypothetical
        ):
            raise ValidationError("Ruling action identity or revision mismatch")
        assessment = self.play.engine.assess(state, action)
        if assessment.status != "adjudication_required":
            raise ValidationError("Action does not require adjudication")
        if any(r.id == command.id for r in state.rulings):
            raise ConflictError("Ruling ID already exists")
        check = self.play.engine.checks.get(("social", action.target_id or ""))
        alternatives = tuple(
            a for a in policy.alternatives if check is not None and a.check_rule_id == check.id
        )
        if not alternatives:
            raise ValidationError("No supported ruling alternatives for this target")
        # All alternatives retain ordinary skill, equipment and resource gates.
        reframed = action.model_copy(update={"approach": "diplomacy"})
        if self.play.engine.assess(state, reframed).status != "feasible":
            raise ValidationError("Ruling alternatives are not feasible")
        ruling = Ruling(
            id=command.id,
            campaign_id=state.campaign_id,
            actor_id=command.actor_id,
            original_action_json=action.model_dump_json(),
            configuration_digest=self.play.engine.digest,
            policy_digest=policy.digest,
            opened_revision=state.revision + 1,
            opened_at=state.resources.game_time,
            valid_revision=state.revision + 1,
            expires_at=state.resources.game_time + policy.lifetime_ticks,
            alternatives=alternatives,
        )
        return state.model_copy(update={"rulings": state.rulings + (ruling,)})

    @staticmethod
    def _lookup(state: PlayState, ruling_id: str) -> Ruling:
        ruling = next((r for r in state.rulings if r.id == ruling_id), None)
        if ruling is None:
            raise ValidationError("Unknown ruling")
        if ruling.current_status(state.revision, state.resources.game_time) == "expired":
            raise ConflictError("Ruling expired after state changed")
        return ruling

    def _decide(self, state: PlayState, command: DecideRuling | EvaluateRuling) -> PlayState:
        ruling = self._lookup(state, command.ruling_id)
        policy = self.play.engine.rules.adjudication
        if policy is None or ruling.status != "pending":
            raise ConflictError("Ruling is not pending")
        authority: Literal["gm", "player", "policy"]
        alternative_id: str | None
        if isinstance(command, EvaluateRuling):
            if not policy.automatic:
                raise ValidationError("Automatic ruling approval is disabled")
            selected = next(
                (
                    a
                    for a in ruling.alternatives
                    if policy.automatic_minimum <= a.modifier <= policy.automatic_maximum
                ),
                None,
            )
            if selected is None:
                raise ValidationError("Alternatives exceed automatic approval bounds")
            approved, alternative_id = True, selected.id
            authority, reason = "policy", "First server-authored alternative within policy bounds"
        else:
            if command.actor_id in self.play.engine.reviewer.gm_ids:
                authority = "gm"
            elif command.actor_id == ruling.actor_id and policy.player_approval:
                authority = "player"
            else:
                raise ValidationError("Ruling decision requires campaign approval authority")
            approved, alternative_id, reason = (
                command.approve,
                command.alternative_id,
                command.reason,
            )
            if approved and alternative_id not in {a.id for a in ruling.alternatives}:
                raise ValidationError("Choose a server-authored ruling alternative")
            if not approved and alternative_id is not None:
                raise ValidationError("Rejected ruling cannot select an alternative")
        updated = ruling.model_copy(
            update={
                "status": "approved" if approved else "rejected",
                "selected_id": alternative_id,
                "approver_id": command.actor_id,
                "authority": authority,
                "reason": reason,
                "valid_revision": state.revision + 1,
                "decided_revision": state.revision + 1,
            }
        )
        return state.model_copy(
            update={"rulings": tuple(updated if r.id == ruling.id else r for r in state.rulings)}
        )

    def _execute(self, state: PlayState, command: ExecuteRuling) -> tuple[PlayState, ActionResult]:
        ruling = self._lookup(state, command.ruling_id)
        if command.actor_id != ruling.actor_id:
            raise ValidationError("Only the affected actor may execute a ruling")
        original = ACTION_ADAPTER.validate_json(ruling.original_action_json)
        if not isinstance(original, Social):
            raise ValidationError("Unsupported ruling action")
        reframed = original.model_copy(
            update={"id": command.id, "expected_revision": state.revision, "approach": "diplomacy"}
        )
        from wayfarer.engine.simulation.party import synchronous

        synchronous(state, command.actor_id)
        updated, resolved_events = self.play.engine.resolve(
            state, reframed, rng=self.play.rng, ruling_id=ruling.id
        )
        result = action_result(resolved_events)
        if result.status != "committed":
            raise ValidationError("Approved action is no longer feasible")
        return updated, result

    async def _commit(
        self, cid: str, command: RulingCommand | EvaluateRuling
    ) -> Ruling | ActionResult:
        if command.hypothetical:
            raise ValidationError("Ruling commands cannot be hypothetical; use action preview")
        payload = json.dumps(
            {"operation": "adjudication", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = self.play._load(campaign)
            result: Ruling | ActionResult
            if isinstance(command, ExecuteRuling):
                updated, result = self._execute(state, command)
            else:
                updated = (
                    self._request(state, command)
                    if isinstance(command, RequestRuling)
                    else self._decide(state, command)
                )
                revision = state.revision + 1
                updated = updated.model_copy(
                    update={
                        "revision": revision,
                        "resources": state.resources.model_copy(update={"revision": revision}),
                        "rulings": expire_rulings(
                            updated.rulings, revision, state.resources.game_time
                        ),
                    }
                )
                ruling_id = command.id if isinstance(command, RequestRuling) else command.ruling_id
                result = next(r for r in updated.rulings if r.id == ruling_id)
            updated = self.play.checkpoint(updated, before=state)
            self.play.commit(campaign, updated)
            return CommandReceipt(action=command.kind, outcome=result.model_dump_json())

        committed = await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
        )
        state = self.play._load(committed["state"])
        if isinstance(command, ExecuteRuling):
            result = state.last_result
            if result is None or result.command_id != command.id:
                raise ValidationError("Missing committed ruling result")
            return result
        ruling_id = command.id if isinstance(command, RequestRuling) else command.ruling_id
        return next(r for r in state.rulings if r.id == ruling_id)
