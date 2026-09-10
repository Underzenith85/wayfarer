"""One world, explicit subgroup clocks, and deterministic scheduled command records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


class Subgroup(Record):
    id: Id
    scene_id: Id
    actor_ids: tuple[Id, ...] = Field(min_length=1)
    ready_through: int = Field(ge=0)
    generation: int = Field(default=0, ge=0)
    paused: bool = False


class QueuedActivity(Record):
    id: Id
    group_id: Id
    actor_id: Id
    generation: int = Field(ge=0)
    start: int = Field(ge=0)
    due: int = Field(ge=0)
    command_json: str = Field(max_length=10000)
    family: Literal["action", "noncombat", "scene", "recovery"]


class ActivityReceipt(Record):
    id: Id
    actor_id: Id
    at: int = Field(ge=0)
    status: Literal["committed", "rejected"]
    # Public receipt contains no private provider or command payload.
    code: str


class CrossSceneEffect(Record):
    id: Id
    source_scene_id: Id
    recipient_actor_ids: tuple[Id, ...] = Field(min_length=1)
    fact_id: Id
    delay: int = Field(default=0, ge=0, le=10000)


class PendingEffect(Record):
    id: Id
    due: int = Field(ge=0)
    fact_id: Id
    recipient_actor_ids: tuple[Id, ...]


class PartyRules(Record):
    id: Id
    version: int = Field(ge=1)
    effects: tuple[CrossSceneEffect, ...] = ()


class PartyState(Record):
    version: Literal[1] = 1
    groups: tuple[Subgroup, ...] = ()
    queue: tuple[QueuedActivity, ...] = ()
    receipts: tuple[ActivityReceipt, ...] = ()
    effects: tuple[PendingEffect, ...] = ()
    fired_effect_ids: tuple[Id, ...] = ()


def migrate(state: PlayState) -> PlayState:
    """Structural migration only; no rule, time, knowledge or resource changes."""
    if state.party.groups or not state.actor_scenes:
        return state
    scenes = sorted({c.scene_id for c in state.actor_scenes})
    groups = tuple(
        Subgroup(
            id=f"group:{scene}",
            scene_id=scene,
            actor_ids=tuple(sorted(c.actor_id for c in state.actor_scenes if c.scene_id == scene)),
            ready_through=state.resources.game_time,
        )
        for scene in scenes
    )
    return state.model_copy(update={"party": PartyState(groups=groups)})


def group_for(state: PlayState, actor_id: str) -> Subgroup:
    group = next((g for g in state.party.groups if actor_id in g.actor_ids), None)
    if group is None:
        raise ValidationError("Actor has no subgroup")
    return group


def synchronous(state: PlayState, actor_id: str) -> None:
    """Legacy immediate mutations must not bypass scheduled concurrent activity."""
    if not state.party.groups:
        return
    group = group_for(state, actor_id)
    if (
        group.paused
        or state.party.queue
        or state.party.effects
        or any(g.ready_through != state.resources.game_time for g in state.party.groups)
    ):
        raise ConflictError("Synchronize subgroup activity before this command")
    if len(state.party.groups) > 1:
        raise ConflictError("Split-party actions require queued activity")


def validate(state: PlayState) -> None:
    party = state.party
    if not party.groups:
        return
    members = [a for g in party.groups for a in g.actor_ids]
    if len(set(members)) != len(members) or set(members) != {a.actor_id for a in state.actors}:
        raise ValidationError("Every actor must belong to exactly one subgroup")
    if len({g.id for g in party.groups}) != len(party.groups):
        raise ValidationError("Duplicate subgroup")
    cursors = {c.actor_id: c.scene_id for c in state.actor_scenes}
    for group in party.groups:
        if group.ready_through < state.resources.game_time:
            raise ValidationError("Subgroup clock precedes committed time")
        if any(cursors.get(a) != group.scene_id for a in group.actor_ids):
            raise ValidationError("Subgroup and actor scenes disagree")
    if len({q.id for q in party.queue}) != len(party.queue):
        raise ValidationError("Duplicate queued activity")
    if len({r.id for r in party.receipts}) != len(party.receipts):
        raise ValidationError("Duplicate activity receipt")
    if {q.id for q in party.queue} & {r.id for r in party.receipts}:
        raise ValidationError("Activity is both queued and completed")
    for activity in party.queue:
        group = group_for(state, activity.actor_id)
        if (
            group.id != activity.group_id
            or group.generation != activity.generation
            or activity.due > group.ready_through
            or activity.due < activity.start
        ):
            raise ValidationError("Invalid queued activity binding")


def validate_effects(rules: PartyRules, state: PlayState, scene_ids: frozenset[str]) -> None:
    """Cross-scene effects must name known scenes, facts and approved recipients."""
    effects = rules.effects
    if len({e.id for e in effects}) != len(effects):
        raise ValidationError("Duplicate cross-scene effect")
    actor_ids = {a.actor_id for a in state.actors}
    facts = {f.id for f in state.world.facts}
    if any(
        e.source_scene_id not in scene_ids
        or e.fact_id not in facts
        or not set(e.recipient_actor_ids) <= actor_ids
        for e in effects
    ):
        raise ValidationError("Invalid cross-scene effect references")
