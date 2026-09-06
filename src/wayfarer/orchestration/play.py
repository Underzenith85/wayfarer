"""Transactional power approvals and typed actions. No model calls in transactions."""

from __future__ import annotations

import json
import secrets

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.character.power import Approval
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event, Roll
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.catalog import reference
from wayfarer.rules.checks import Outcome, RandomSource
from wayfarer.simulation.actions import (
    ACTION_ADAPTER,
    ActionEngine,
    ActionResult,
    ActorSetup,
    PlayActor,
    PlayState,
    TypedAction,
)
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.resources import Pool, Record, ResourceState
from wayfarer.world import World


class ApproveCharacter(Record):
    id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=100)
    target_actor_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class PlayService:
    def __init__(
        self,
        store: AsyncSQLiteStore | AsyncPostgresStore,
        engine: ActionEngine,
        *,
        rng: RandomSource = secrets,
    ) -> None:
        self.store, self.engine, self.rng = store, engine, rng

    async def create(
        self,
        campaign: Campaign,
        world: World,
        resources: ResourceState,
        actors: tuple[ActorSetup, ...],
    ) -> PlayState:
        """Trusted scenario input; player drafts never supply approval records."""
        if campaign["revision"] != 0 or resources.revision != 0:
            raise ValidationError("Initial revisions must be zero")
        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        if "resources_json" in campaign or "play_json" in campaign:
            raise ValidationError("Campaign already has an engine checkpoint")
        approvals: list[Approval] = []
        play_actors: list[PlayActor] = []
        owners = {o.actor_id: o for o in resources.owners}
        pools = {p.id: p for p in resources.pools}
        for actor in actors:
            if actor.actor_id not in owners:
                raise ValidationError("Play actor needs a resource owner")
            review = self.engine.reviewer.review(actor.proposal)
            build = review.compilation.build
            if build is None:
                raise ValidationError("Scenario character is point-illegal")
            values = {v.target: int(v.value) for v in build.sheet.values}
            if not {"attribute:st", "attribute:ht"} <= values.keys():
                raise ValidationError("Character has no supported runtime resources")
            owners[actor.actor_id] = owners[actor.actor_id].model_copy(
                update={"definitions": tuple(p.definition_id for p in build.purchases)}
            )
            for name, attribute in (("hp", "attribute:st"), ("fp", "attribute:ht")):
                key = f"{name}:{actor.actor_id}"
                pools[key] = Pool(id=key, current=values[attribute], maximum=values[attribute])
            approval = None
            if review.status == "automatic":
                approval = self.engine.reviewer.approve(
                    actor.proposal, campaign_id=campaign["id"], actor_id=actor.actor_id, revision=0
                )
                approvals.append(approval)
            play_actors.append(
                PlayActor(
                    actor_id=actor.actor_id,
                    proposal=actor.proposal,
                    aware_of=actor.aware_of,
                    conditions=actor.conditions,
                    available_at=actor.available_at,
                    approval=approval,
                )
            )
        resources = resources.model_copy(
            update={"owners": tuple(owners.values()), "pools": tuple(pools.values())}
        )
        state = PlayState(
            campaign_id=campaign["id"],
            configuration_digest=self.engine.digest,
            world=world,
            resources=resources,
            actors=tuple(play_actors),
            approvals=tuple(approvals),
        )
        self.engine.validate(state)
        stored = campaign.copy()
        stored["play_json"] = state.model_dump_json()
        await self.store.insert(stored)
        return state

    def _load(self, campaign: Campaign) -> PlayState:
        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        raw = campaign.get("play_json")
        if raw is None:
            raise ValidationError("Campaign has no typed play state")
        state = PlayState.model_validate_json(raw)
        if state.campaign_id != campaign["id"] or state.revision != campaign["revision"]:
            raise ValidationError("Campaign and play checkpoint disagree")
        self.engine.validate(state)
        return state

    @staticmethod
    def propose(value: object) -> TypedAction:
        try:
            return ACTION_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid typed action proposal") from exc

    @staticmethod
    def _authorize(command: TypedAction, authenticated_actor_id: str) -> None:
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Command actor is not authorized")

    async def preview(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> ActionResult:
        command = self.propose(value)
        self._authorize(command, authenticated_actor_id)
        return self.engine.assess(self._load(await self.store.read(cid)), command)

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> ActionResult:
        command = self.propose(value)
        self._authorize(command, authenticated_actor_id)
        payload = json.dumps(
            {"operation": "typed-action", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            result = self._load(duplicate).last_result
            if result is None:
                raise ValidationError("Missing committed action result")
            return result
        feasible = self.engine.assess(self._load(await self.store.read(cid)), command)
        if feasible.status != "feasible":
            return feasible

        def resolve(campaign: Campaign) -> Event:
            state, result = self.engine.resolve(self._load(campaign), command, rng=self.rng)
            if result.status != "committed":
                raise ValidationError("Action is no longer feasible")
            campaign["play_json"] = state.model_dump_json()
            campaign["revision"] = state.revision
            roll: Roll | None = None
            if result.check is not None:
                check = result.check
                roll = Roll(
                    dice=list(check.dice),
                    total=check.total,
                    target=check.effective_target,
                    success=check.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS),
                    critical="success"
                    if check.outcome is Outcome.CRITICAL_SUCCESS
                    else "failure"
                    if check.outcome is Outcome.CRITICAL_FAILURE
                    else None,
                )
            return Event(
                input=payload, action="typed-action", outcome=result.model_dump_json(), roll=roll
            )

        committed = await self.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        result = self._load(committed["state"]).last_result
        if result is None:
            raise ValidationError("Missing committed action result")
        return result

    async def approve(self, cid: str, value: object, *, authenticated_gm_id: str) -> Approval:
        try:
            command = ApproveCharacter.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid approval command") from exc
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.engine.reviewer.gm_ids
        ):
            raise ValidationError("Approval requires GM authority")
        payload = json.dumps(
            {"operation": "power-approval", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> Event:
            state = self._load(campaign)
            actor = next((a for a in state.actors if a.actor_id == command.target_actor_id), None)
            if actor is None:
                raise ValidationError("Unknown approval target")
            approval = self.engine.reviewer.approve(
                actor.proposal,
                campaign_id=cid,
                actor_id=actor.actor_id,
                revision=state.revision + 1,
                approver_id=authenticated_gm_id,
                reason=command.reason,
            )
            updated = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "resources": state.resources.model_copy(
                        update={"revision": state.revision + 1}
                    ),
                    "actors": tuple(
                        a.model_copy(update={"approval": approval})
                        if a.actor_id == actor.actor_id
                        else a
                        for a in state.actors
                    ),
                    "approvals": state.approvals + (approval,),
                    "rulings": expire_rulings(
                        state.rulings, state.revision + 1, state.resources.game_time
                    ),
                }
            )
            self.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload,
                action="power-approval",
                outcome=approval.model_dump_json(),
                roll=None,
            )

        committed = await self.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=authenticated_gm_id,
        )
        state = self._load(committed["state"])
        approval = next(a.approval for a in state.actors if a.actor_id == command.target_actor_id)
        if approval is None:
            raise ValidationError("Missing committed approval")
        return approval
