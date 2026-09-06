"""Authorized campaign queries, commands, and perspective-safe resumable streams."""

from __future__ import annotations

import json
from dataclasses import asdict, replace

from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.objectives import ObjectiveCommand, ObjectiveService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService
from wayfarer.simulation.access import CampaignMember, StreamEvent
from wayfarer.simulation.actions import ACTION_ADAPTER, PlayState


class CampaignAccess:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    @staticmethod
    def _member(state: PlayState, principal_id: str) -> CampaignMember:
        member = next((item for item in state.members if item.principal_id == principal_id), None)
        if member is None:
            # Avoid disclosing whether an inaccessible campaign exists.
            raise NotFoundError("Campaign not found")
        return member

    @staticmethod
    def _projection(state: PlayState, member: CampaignMember) -> dict[str, object]:
        if member.role == "gm":
            return {
                "campaign_id": state.campaign_id,
                "revision": state.revision,
                "game_time": state.resources.game_time,
                "role": member.role,
                "actors": tuple(actor.actor_id for actor in state.actors),
                "world": asdict(state.world),
            }
        perspectives: dict[str, object] = {}
        for actor_id in member.actor_ids:
            own = next(e for e in state.world.entities if e.id == actor_id)
            perspective = state.world.perspective(actor_id)
            known = {f.id for f in perspective.facts}
            perspective = replace(
                perspective,
                commitments=tuple(
                    c
                    for c in perspective.commitments
                    if c.reveal_fact_id in known
                    or (c.reveal_fact_id is None and actor_id in (c.debtor_id, c.creditor_id))
                ),
            )
            perspective = replace(
                perspective,
                entities=tuple(
                    e
                    if e.id == actor_id or e.location_id == own.location_id
                    else replace(e, location_id=None, owner_id=None)
                    for e in perspective.entities
                ),
            )
            perspectives[actor_id] = asdict(perspective)
        groups = tuple(g for g in state.party.groups if set(g.actor_ids) & set(member.actor_ids))
        visible_objectives = state.objectives.model_dump(
            mode="json", exclude={"evidence", "settled_reward_ids"}
        )
        visible_objectives["progress"] = tuple(
            {"objective_id": e.objective_id, "satisfied": e.satisfied}
            for e in state.objectives.evidence
            if not e.visible_to or set(e.visible_to) & set(member.actor_ids)
        )
        return {
            "campaign_id": state.campaign_id,
            "revision": state.revision,
            "game_time": state.resources.game_time,
            "role": member.role,
            "actors": member.actor_ids,
            "inventory": tuple(
                i.model_dump(mode="json")
                for i in state.resources.items
                if i.owner_id in member.actor_ids
            ),
            "status": tuple(
                a.model_dump(mode="json", include={"actor_id", "conditions", "available_at"})
                for a in state.actors
                if a.actor_id in member.actor_ids
            ),
            "pools": tuple(
                p.model_dump(mode="json")
                for p in state.resources.pools
                if any(p.id == f"{kind}:{a}" for a in member.actor_ids for kind in ("hp", "fp"))
            ),
            "perspectives": perspectives,
            "subgroups": tuple(g.model_dump(mode="json") for g in groups),
            "pending_activities": tuple(
                q.model_dump(mode="json", exclude={"command_json"})
                for q in state.party.queue
                if q.actor_id in member.actor_ids
            ),
            "activity_receipts": tuple(
                r.model_dump(mode="json")
                for r in state.party.receipts
                if r.actor_id in member.actor_ids
            ),
            "noncombat": tuple(
                e.model_dump(mode="json") for e in state.noncombat if e.actor_id in member.actor_ids
            ),
            "objectives": visible_objectives,
        }

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]:
        state = self.play._load(await self.play.store.read(cid))
        return self._projection(state, self._member(state, principal_id))

    async def execute(self, cid: str, value: object, *, principal_id: str) -> dict[str, object]:
        state = self.play._load(await self.play.store.read(cid))
        member = self._member(state, principal_id)
        if not isinstance(value, dict):
            raise ValidationError("Invalid typed campaign command")
        kind = value.get("kind")
        raw = json.dumps(value)
        try:
            if kind in (
                "start_encounter",
                "take_combat_turn",
                "choose_defense",
                "end_encounter",
                "join_encounter",
            ):
                combat = COMBAT_ADAPTER.validate_json(raw)
                self._control(member, combat.actor_id)
                await CombatService(self.play).execute(
                    cid, combat, authenticated_actor_id=combat.actor_id
                )
            elif kind in ("observe_scene", "travel_scene"):
                scene = SCENE_ADAPTER.validate_json(raw)
                self._control(member, scene.actor_id)
                await SceneService(self.play).execute(
                    cid, scene, authenticated_actor_id=scene.actor_id
                )
            elif kind in ("start_noncombat", "approach_noncombat", "withdraw_noncombat"):
                noncombat = NoncombatCommand.model_validate_json(raw)
                self._control(member, noncombat.actor_id)
                await NoncombatService(self.play).execute(
                    cid, noncombat, authenticated_actor_id=noncombat.actor_id
                )
            elif kind in ("evaluate_objectives", "abandon_scenario"):
                objective = ObjectiveCommand.model_validate_json(raw)
                self._control(member, objective.actor_id)
                await ObjectiveService(self.play).execute(
                    cid, objective, authenticated_actor_id=objective.actor_id
                )
            elif kind in (
                "split_party",
                "rejoin_party",
                "queue_activity",
                "pause_group",
                "resume_group",
                "signal_scene",
                "transfer_item",
            ):
                party = PartyCommand.model_validate_json(raw)
                self._control(member, party.actor_id)
                await PartyService(self.play).execute(
                    cid, party, authenticated_actor_id=party.actor_id
                )
            else:
                command = ACTION_ADAPTER.validate_json(raw)
                self._control(member, command.actor_id)
                await self.play.execute(cid, command, authenticated_actor_id=command.actor_id)
        except ValueError as exc:
            raise ValidationError("Invalid typed campaign command") from exc
        return await self.read(cid, principal_id=principal_id)

    @staticmethod
    def _control(member: CampaignMember, actor_id: str) -> None:
        if not (
            (member.role == "player" and actor_id in member.actor_ids)
            or (member.role == "gm" and actor_id == member.principal_id)
        ):
            raise AuthorizationError("Principal cannot control this actor")

    async def events(
        self, cid: str, *, principal_id: str, after: int = 0, limit: int = 100
    ) -> tuple[StreamEvent, ...]:
        if after < 0 or not 1 <= limit <= 100:
            raise ValidationError("Invalid stream cursor or limit")
        current = self.play._load(await self.play.store.read(cid))
        member = self._member(current, principal_id)
        history = await self.play.store.history(cid)
        if after > current.revision:
            raise ConflictError("Stream cursor is ahead of campaign")
        result: list[StreamEvent] = []
        for event in history:
            if event.resulting_revision <= after:
                continue
            state = PlayState.model_validate_json(event.state_after["play_json"])
            result.append(
                StreamEvent(
                    cursor=event.resulting_revision,
                    command_id=event.command_id
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "redacted",
                    actor_id=event.actor_id
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "redacted",
                    action=event.event["action"]
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "private",
                    outcome=(
                        event.event["outcome"]
                        if member.role == "gm"
                        or (
                            event.actor_id in member.actor_ids
                            and event.event["action"] != "objectives"
                        )
                        else ""
                    ),
                    projection=self._projection(state, member),
                )
            )
            if len(result) == limit:
                break
        return tuple(result)
