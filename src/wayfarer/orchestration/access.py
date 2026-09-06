"""Authorized campaign queries, commands, and perspective-safe resumable streams."""

from __future__ import annotations

from dataclasses import asdict

from wayfarer.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.play import PlayService
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
        perspectives = {
            actor_id: asdict(state.world.perspective(actor_id)) for actor_id in member.actor_ids
        }
        return {
            "campaign_id": state.campaign_id,
            "revision": state.revision,
            "game_time": state.resources.game_time,
            "role": member.role,
            "actors": member.actor_ids,
            "perspectives": perspectives,
        }

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]:
        state = self.play._load(await self.play.store.read(cid))
        return self._projection(state, self._member(state, principal_id))

    async def execute(self, cid: str, value: object, *, principal_id: str) -> dict[str, object]:
        state = self.play._load(await self.play.store.read(cid))
        member = self._member(state, principal_id)
        try:
            command = ACTION_ADAPTER.validate_python(value)
        except ValueError as exc:
            raise ValidationError("Invalid typed action command") from exc
        if member.role != "player" or command.actor_id not in member.actor_ids:
            raise AuthorizationError("Principal cannot control this actor")
        await self.play.execute(cid, command, authenticated_actor_id=command.actor_id)
        return await self.read(cid, principal_id=principal_id)

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
                    command_id=event.command_id,
                    actor_id=event.actor_id,
                    action=event.event["action"],
                    outcome=(
                        event.event["outcome"]
                        if member.role == "gm" or event.actor_id in member.actor_ids
                        else ""
                    ),
                    projection=self._projection(state, member),
                )
            )
            if len(result) == limit:
                break
        return tuple(result)
