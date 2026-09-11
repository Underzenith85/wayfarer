"""Versioned authored scene graph, discovery, and journal contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record
from wayfarer.world import EntityKind, World

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


class SceneExit(Record):
    id: Id
    destination_id: Id
    ticks: int = Field(default=1, ge=1, le=10000)
    required_fact_ids: tuple[Id, ...] = ()


class Obstacle(Record):
    id: Id
    exit_id: Id
    description: str = Field(min_length=1, max_length=1000)
    bypass_fact_ids: tuple[Id, ...] = ()


class Discovery(Record):
    id: Id
    scene_id: Id
    fact_id: Id
    mode: Literal["automatic", "check"]
    target_id: Id | None = None


class SceneTrigger(Record):
    id: Id
    scene_id: Id
    phase: Literal["entry", "exit"]
    fact_id: Id


class Scene(Record):
    id: Id
    version: int = Field(ge=1)
    location_id: Id
    title: str = Field(min_length=1, max_length=200)
    exits: tuple[SceneExit, ...] = ()
    obstacles: tuple[Obstacle, ...] = ()

    @model_validator(mode="after")
    def valid_local_references(self) -> Scene:
        exits = {value.id for value in self.exits}
        if len(exits) != len(self.exits) or len({value.id for value in self.obstacles}) != len(
            self.obstacles
        ):
            raise ValueError("Duplicate scene-local ID")
        if any(value.exit_id not in exits for value in self.obstacles):
            raise ValueError("Obstacle references an unknown exit")
        return self


class SceneRules(Record):
    id: Id
    version: int = Field(ge=1)
    scenes: tuple[Scene, ...] = Field(min_length=1)
    discoveries: tuple[Discovery, ...] = ()
    triggers: tuple[SceneTrigger, ...] = ()

    def validate_world(self, world: World) -> None:
        scene_ids = {scene.id for scene in self.scenes}
        locations = {entity.id for entity in world.entities if entity.kind is EntityKind.LOCATION}
        facts = {fact.id for fact in world.facts}
        entities = {entity.id for entity in world.entities}
        exits = {value.id for scene in self.scenes for value in scene.exits}
        if len(scene_ids) != len(self.scenes):
            raise ValidationError("Duplicate scene ID")
        if any(scene.location_id not in locations for scene in self.scenes):
            raise ValidationError("Scene requires a world location")
        if any(
            value.destination_id not in scene_ids for scene in self.scenes for value in scene.exits
        ):
            raise ValidationError("Exit references an unknown scene")
        if any(
            not set(value.required_fact_ids) <= facts
            for scene in self.scenes
            for value in scene.exits
        ):
            raise ValidationError("Exit references an unknown fact")
        if any(
            not set(value.bypass_fact_ids) <= facts
            for scene in self.scenes
            for value in scene.obstacles
        ):
            raise ValidationError("Obstacle references an unknown fact")
        if len({value.id for value in self.discoveries}) != len(self.discoveries) or any(
            value.scene_id not in scene_ids
            or value.fact_id not in facts
            or (value.mode == "check" and value.target_id not in entities)
            or (value.mode == "automatic" and value.target_id is not None)
            for value in self.discoveries
        ):
            raise ValidationError("Invalid scene discovery")
        if len({value.id for value in self.triggers}) != len(self.triggers) or any(
            value.scene_id not in scene_ids or value.fact_id not in facts for value in self.triggers
        ):
            raise ValidationError("Invalid scene trigger")
        if any(
            obstacle.exit_id not in exits for scene in self.scenes for obstacle in scene.obstacles
        ):
            raise ValidationError("Invalid obstacle exit")


class ActorScene(Record):
    actor_id: Id
    scene_id: Id


class SceneEvent(Record):
    id: Id
    actor_id: Id
    scene_id: Id
    kind: Literal["entered", "exited", "discovered", "observed"]
    fact_id: str | None = None
    at: int = Field(ge=0)
    revision: int = Field(default=0, ge=0)
    revealed_fact_ids: tuple[str, ...] = ()


class JournalEntry(Record):
    id: Id
    actor_id: Id
    scene_id: Id
    fact_id: Id
    at: int = Field(ge=0)


def validate_state(rules: SceneRules | None, state: PlayState) -> None:
    """Scene cursors, events, journal and fired triggers must all resolve to the scene rules."""
    if rules is None:
        if state.actor_scenes or state.scene_events or state.journal or state.fired_scene_triggers:
            raise ValidationError("Campaign has scene state without scene rules")
        return
    rules.validate_world(state.world)
    scenes = {scene.id for scene in rules.scenes}
    actor_ids = {actor.actor_id for actor in state.actors}
    if len({value.actor_id for value in state.actor_scenes}) != len(state.actor_scenes):
        raise ValidationError("Duplicate actor scene cursor")
    if {value.actor_id for value in state.actor_scenes} != actor_ids:
        raise ValidationError("Every actor requires one scene cursor")
    if any(value.scene_id not in scenes for value in state.actor_scenes):
        raise ValidationError("Unknown actor scene")
    if len({value.id for value in state.scene_events}) != len(state.scene_events):
        raise ValidationError("Duplicate scene event")
    if any(
        value.actor_id not in actor_ids
        or value.scene_id not in scenes
        or value.revision > state.revision
        for value in state.scene_events
    ):
        raise ValidationError("Invalid scene event")
    if len({value.id for value in state.journal}) != len(state.journal):
        raise ValidationError("Duplicate journal entry")
    facts = {fact.id for fact in state.world.facts}
    if any(
        value.actor_id not in actor_ids
        or value.scene_id not in scenes
        or value.fact_id not in facts
        or (value.actor_id, value.fact_id) not in state.world.knowledge
        for value in state.journal
    ):
        raise ValidationError("Invalid perspective journal entry")
    trigger_ids = {value.id for value in rules.triggers}
    if (
        len(set(state.fired_scene_triggers)) != len(state.fired_scene_triggers)
        or not set(state.fired_scene_triggers) <= trigger_ids
    ):
        raise ValidationError("Invalid fired scene trigger")
