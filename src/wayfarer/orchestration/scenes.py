"""Transactional travel, automatic observation, triggers, and perspective journals."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Literal

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.actions import ActionCommand, PlayState
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.resources import Advance
from wayfarer.simulation.scenes import ActorScene, JournalEntry, Scene, SceneEvent
from wayfarer.world import EntityKind


class ObserveScene(ActionCommand):
    kind: Literal["observe_scene"] = "observe_scene"


class TravelScene(ActionCommand):
    kind: Literal["travel_scene"] = "travel_scene"
    exit_id: str = Field(min_length=1, max_length=100)


SceneCommand = ObserveScene | TravelScene
SCENE_ADAPTER: TypeAdapter[SceneCommand] = TypeAdapter(SceneCommand)


class SceneService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def _rules(self) -> tuple[Scene, ...]:
        rules = self.play.engine.rules.scenes
        if rules is None:
            raise ValidationError("Campaign scenes are not configured")
        return rules.scenes

    async def execute(self, cid: str, value: object, *, authenticated_actor_id: str) -> SceneEvent:
        try:
            command = SCENE_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid scene command") from exc
        if command.actor_id != authenticated_actor_id or command.hypothetical:
            raise ValidationError("Scene command actor is not authorized")
        payload = json.dumps(
            {"operation": "scene", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.play.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            result = self._result(PlayState.model_validate_json(duplicate["play_json"]), command.id)
            return result

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            if isinstance(command, TravelScene):
                from wayfarer.simulation.party import synchronous

                synchronous(state, command.actor_id)
            updated = self.play.checkpoint(self.reduce(state, command), before=state)
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            result = self._result(updated, command.id)
            return Event(input=payload, action="scene", outcome=result.model_dump_json(), roll=None)

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return self._result(
            PlayState.model_validate_json(committed["state"]["play_json"]), command.id
        )

    def reduce(
        self, state: PlayState, command: SceneCommand, *, advance_time: bool = True
    ) -> PlayState:
        from wayfarer.orchestration.recovery import guard

        guard(state, command.actor_id, command.kind)
        rules = self.play.engine.rules.scenes
        if rules is None:
            raise ValidationError("Campaign scenes are not configured")
        cursor = next(
            (item for item in state.actor_scenes if item.actor_id == command.actor_id), None
        )
        if cursor is None:
            raise ValidationError("Actor has no scene cursor")
        scene = next(value for value in rules.scenes if value.id == cursor.scene_id)
        known = {fact for actor, fact in state.world.knowledge if actor == command.actor_id}
        world, resources = state.world, state.resources
        fired = set(state.fired_scene_triggers)
        events = list(state.scene_events)
        journal = list(state.journal)
        revealed: list[str] = []
        destination = scene
        event_kind: Literal["entered", "exited", "discovered", "observed"] = "observed"
        if isinstance(command, TravelScene):
            selected = next((value for value in scene.exits if value.id == command.exit_id), None)
            if selected is None:
                raise ValidationError("Unknown exit from current scene")
            obstacles = tuple(value for value in scene.obstacles if value.exit_id == selected.id)
            if not set(selected.required_fact_ids) <= known or any(
                value.bypass_fact_ids and not set(value.bypass_fact_ids) <= known
                for value in obstacles
            ):
                raise ConflictError("Exit is blocked")
            destination = next(
                value for value in rules.scenes if value.id == selected.destination_id
            )
            for trigger in rules.triggers:
                if (
                    trigger.scene_id == scene.id
                    and trigger.phase == "exit"
                    and trigger.id not in fired
                ):
                    world = world.learn(command.actor_id, trigger.fact_id)
                    fired.add(trigger.id)
                    revealed.append(trigger.fact_id)
            world = replace(
                world,
                entities=tuple(
                    replace(entity, location_id=destination.location_id)
                    if entity.id == command.actor_id and entity.kind is EntityKind.ACTOR
                    else entity
                    for entity in world.entities
                ),
            )
            if advance_time:
                resources = self.play.engine.resources.apply(
                    resources,
                    Advance(
                        id=f"{command.id}:time",
                        actor_id=command.actor_id,
                        expected_revision=resources.revision,
                        to=resources.game_time + selected.ticks,
                    ),
                    system=True,
                )
            events.append(
                SceneEvent(
                    id=f"{command.id}:exit",
                    actor_id=command.actor_id,
                    scene_id=scene.id,
                    kind="exited",
                    at=resources.game_time,
                )
            )
            event_kind = "entered"
        for discovery in rules.discoveries:
            if (
                discovery.scene_id == destination.id
                and discovery.mode == "automatic"
                and discovery.fact_id not in known
            ):
                world = world.learn(command.actor_id, discovery.fact_id)
                revealed.append(discovery.fact_id)
                journal.append(
                    JournalEntry(
                        id=f"{command.id}:{discovery.id}",
                        actor_id=command.actor_id,
                        scene_id=destination.id,
                        fact_id=discovery.fact_id,
                        at=resources.game_time,
                    )
                )
        if isinstance(command, TravelScene):
            for trigger in rules.triggers:
                if (
                    trigger.scene_id == destination.id
                    and trigger.phase == "entry"
                    and trigger.id not in fired
                ):
                    world = world.learn(command.actor_id, trigger.fact_id)
                    fired.add(trigger.id)
                    revealed.append(trigger.fact_id)
        event = SceneEvent(
            id=command.id,
            actor_id=command.actor_id,
            scene_id=destination.id,
            kind=event_kind,
            at=resources.game_time,
        )
        events.append(event)
        revision = state.revision + 1
        resources = resources.model_copy(update={"revision": revision})
        updated = state.model_copy(
            update={
                "revision": revision,
                "world": world,
                "resources": resources,
                "actor_scenes": tuple(
                    ActorScene(actor_id=value.actor_id, scene_id=destination.id)
                    if value.actor_id == command.actor_id
                    else value
                    for value in state.actor_scenes
                ),
                "scene_events": tuple(events),
                "journal": tuple(journal),
                "fired_scene_triggers": tuple(sorted(fired)),
                "rulings": expire_rulings(state.rulings, revision, resources.game_time),
            }
        )
        result = event.model_copy(
            update={
                "revision": revision,
                "revealed_fact_ids": tuple(dict.fromkeys(revealed)),
            }
        )
        events[-1] = result
        updated = updated.model_copy(update={"scene_events": tuple(events)})
        if updated.party.groups:
            from wayfarer.simulation.party import group_for

            group = group_for(state, command.actor_id)
            if isinstance(command, TravelScene) and len(group.actor_ids) != 1:
                raise ConflictError("Split before moving an individual actor")
            updated = updated.model_copy(
                update={
                    "party": updated.party.model_copy(
                        update={
                            "groups": tuple(
                                g.model_copy(
                                    update={
                                        "scene_id": destination.id,
                                        "ready_through": max(g.ready_through, resources.game_time),
                                    }
                                )
                                if command.actor_id in g.actor_ids
                                else g
                                for g in updated.party.groups
                            )
                        }
                    )
                }
            )
        return updated

    @staticmethod
    def _result(state: PlayState, command_id: str) -> SceneEvent:
        event = next(
            (value for value in reversed(state.scene_events) if value.id == command_id), None
        )
        if event is None:
            raise ValidationError("Missing committed scene result")
        return event

    def journal(self, state: PlayState, actor_id: str) -> tuple[JournalEntry, ...]:
        self._rules()
        return tuple(entry for entry in state.journal if entry.actor_id == actor_id)
