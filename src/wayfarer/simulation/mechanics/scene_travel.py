"""Pure scene travel used by physical-route completion."""

from dataclasses import replace

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.mechanics.location_combat import disabled
from wayfarer.simulation.mechanics.recovery_guard import guard
from wayfarer.simulation.party import group_for
from wayfarer.simulation.rules_context import RulesContext
from wayfarer.simulation.scenes import ActorScene, JournalEntry, SceneEvent
from wayfarer.world import EntityKind


def travel_scene(
    state: PlayState,
    *,
    actor_id: str,
    command_id: str,
    exit_id: str,
    runtime: RulesContext,
    group_travel: bool,
    revision: int,
) -> PlayState:
    """Traverse one authored exit without charging a second time interval."""
    guard(state, actor_id, "travel_scene")
    rules = runtime.rules.scenes
    if rules is None:
        raise ValidationError("Campaign scenes are not configured")
    cursor = next((item for item in state.actor_scenes if item.actor_id == actor_id), None)
    if cursor is None:
        raise ValidationError("Actor has no scene cursor")
    scene = next(value for value in rules.scenes if value.id == cursor.scene_id)
    known = {fact for owner, fact in state.world.knowledge if owner == actor_id}
    if disabled(state, actor_id) & {"left-leg", "right-leg", "left-foot", "right-foot"}:
        raise ValidationError("Scene travel requires supported mobility after crippling")
    selected = next((value for value in scene.exits if value.id == exit_id), None)
    if selected is None:
        raise ValidationError("Unknown exit from current scene")
    obstacles = tuple(value for value in scene.obstacles if value.exit_id == selected.id)
    if not set(selected.required_fact_ids) <= known or any(
        value.bypass_fact_ids and not set(value.bypass_fact_ids) <= known for value in obstacles
    ):
        raise ConflictError("Exit is blocked")
    destination = next(value for value in rules.scenes if value.id == selected.destination_id)
    world = state.world
    fired = set(state.fired_scene_triggers)
    events = list(state.scene_events)
    journal = list(state.journal)
    revealed: list[str] = []
    for trigger in rules.triggers:
        if trigger.scene_id == scene.id and trigger.phase == "exit" and trigger.id not in fired:
            world = world.learn(actor_id, trigger.fact_id)
            fired.add(trigger.id)
            revealed.append(trigger.fact_id)
    world = replace(
        world,
        entities=tuple(
            replace(entity, location_id=destination.location_id)
            if entity.id == actor_id and entity.kind is EntityKind.ACTOR
            else entity
            for entity in world.entities
        ),
    )
    events.append(
        SceneEvent(
            id=f"{command_id}:exit",
            actor_id=actor_id,
            scene_id=scene.id,
            kind="exited",
            at=state.resources.game_time,
        )
    )
    for discovery in rules.discoveries:
        if (
            discovery.scene_id == destination.id
            and discovery.mode == "automatic"
            and discovery.fact_id not in known
        ):
            world = world.learn(actor_id, discovery.fact_id)
            revealed.append(discovery.fact_id)
            journal.append(
                JournalEntry(
                    id=f"{command_id}:{discovery.id}",
                    actor_id=actor_id,
                    scene_id=destination.id,
                    fact_id=discovery.fact_id,
                    at=state.resources.game_time,
                )
            )
    for trigger in rules.triggers:
        if (
            trigger.scene_id == destination.id
            and trigger.phase == "entry"
            and trigger.id not in fired
        ):
            world = world.learn(actor_id, trigger.fact_id)
            fired.add(trigger.id)
            revealed.append(trigger.fact_id)
    event = SceneEvent(
        id=command_id,
        actor_id=actor_id,
        scene_id=destination.id,
        kind="entered",
        at=state.resources.game_time,
        revision=revision,
        revealed_fact_ids=tuple(dict.fromkeys(revealed)),
    )
    events.append(event)
    resources = state.resources.model_copy(update={"revision": revision})
    updated = state.model_copy(
        update={
            "revision": revision,
            "world": world,
            "resources": resources,
            "actor_scenes": tuple(
                ActorScene(actor_id=value.actor_id, scene_id=destination.id)
                if value.actor_id == actor_id
                else value
                for value in state.actor_scenes
            ),
            "scene_events": tuple(events),
            "journal": tuple(journal),
            "fired_scene_triggers": tuple(sorted(fired)),
            "rulings": expire_rulings(state.rulings, revision, resources.game_time),
        }
    )
    if updated.party.groups:
        group = group_for(state, actor_id)
        if len(group.actor_ids) != 1 and not group_travel:
            raise ConflictError("Split before moving an individual actor")
        updated = updated.model_copy(
            update={
                "party": updated.party.model_copy(
                    update={
                        "groups": tuple(
                            value.model_copy(
                                update={
                                    "scene_id": destination.id,
                                    "ready_through": max(value.ready_through, resources.game_time),
                                }
                            )
                            if actor_id in value.actor_ids
                            else value
                            for value in updated.party.groups
                        )
                    }
                )
            }
        )
    return updated
