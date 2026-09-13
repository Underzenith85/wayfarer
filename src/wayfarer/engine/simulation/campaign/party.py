"""One world, explicit subgroup clocks, and deterministic scheduled command records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from wayfarer.engine.simulation.magic.enchanting import busy_actor_ids as enchanting_actor_ids
from wayfarer.engine.simulation.projects.inventions import busy_actor_ids
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState


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


class LeadershipActivityRule(Record):
    """A GM-authored NPC group activity gated by one B204 trigger."""

    id: Id
    trigger_id: Id
    group_id: Id
    activity_id: Id
    follower_actor_ids: tuple[Id, ...] = Field(min_length=1)


class LeadershipActivity(Record):
    id: Id
    rule_id: Id
    activity_id: Id
    group_id: Id
    leader_actor_id: Id
    follower_actor_ids: tuple[Id, ...]
    group_size: int = Field(ge=2)
    group_size_modifier: Literal[0] = 0
    status: Literal["followed", "hesitant", "refused"]


class PartyRules(Record):
    id: Id
    version: int = Field(ge=1)
    effects: tuple[CrossSceneEffect, ...] = ()
    leadership: tuple[LeadershipActivityRule, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


class PartyState(Record):
    version: Literal[1] = 1
    groups: tuple[Subgroup, ...] = ()
    queue: tuple[QueuedActivity, ...] = ()
    receipts: tuple[ActivityReceipt, ...] = ()
    effects: tuple[PendingEffect, ...] = ()
    fired_effect_ids: tuple[Id, ...] = ()
    leadership: tuple[LeadershipActivity, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


def bind_leadership_outcome(
    rules: PartyRules | None,
    party: PartyState,
    *,
    command_id: str,
    trigger_id: str,
    leader_actor_id: str,
    subject_id: str,
    outcome: str,
    player_actor_ids: frozenset[str],
) -> PartyState:
    """Bind a B204 verdict to an authored NPC group activity.

    B204 gives no numerical group-size penalty. ``group_size_modifier`` records
    that audited zero while the result names every affected follower. PCs remain
    outside the consequence and choose their own actions.
    """
    if rules is None:
        return party
    rule = next((value for value in rules.leadership if value.trigger_id == trigger_id), None)
    if rule is None:
        return party
    group = next((value for value in party.groups if value.id == rule.group_id), None)
    if group is None or leader_actor_id not in group.actor_ids:
        raise ValidationError("Leadership activity requires the leader's current subgroup")
    followers = frozenset(rule.follower_actor_ids)
    if subject_id not in followers or not followers < frozenset(group.actor_ids):
        raise ValidationError("Leadership subject and followers must share the subgroup")
    if followers & player_actor_ids:
        raise ValidationError("Leadership cannot select a player character's group activity")
    if any(value.id == command_id for value in party.leadership):
        raise ConflictError("Leadership outcome is already bound")
    status: Literal["followed", "hesitant", "refused"] = (
        "followed"
        if outcome == "leadership-followed"
        else "refused"
        if outcome == "leadership-refused"
        else "hesitant"
    )
    return party.model_copy(
        update={
            "leadership": party.leadership
            + (
                LeadershipActivity(
                    id=command_id,
                    rule_id=rule.id,
                    activity_id=rule.activity_id,
                    group_id=rule.group_id,
                    leader_actor_id=leader_actor_id,
                    follower_actor_ids=rule.follower_actor_ids,
                    group_size=1 + len(rule.follower_actor_ids),
                    status=status,
                ),
            ),
            "receipts": party.receipts
            + (
                ActivityReceipt(
                    id="leadership:" + command_id,
                    actor_id=leader_actor_id,
                    at=group.ready_through,
                    status="committed" if status == "followed" else "rejected",
                    code=rule.activity_id,
                ),
            ),
        }
    )


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
    if actor_id in busy_actor_ids(state.resources.inventions):
        raise ConflictError("Actor is committed to full-time invention work")
    if actor_id in enchanting_actor_ids(state.resources.enchantment_projects):
        raise ConflictError("Actor is committed to enchanting work")
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
    if len({value.id for value in party.leadership}) != len(party.leadership):
        raise ValidationError("Duplicate Leadership activity")
    groups = {value.id: value for value in party.groups}
    for result in party.leadership:
        leader_group = groups.get(result.group_id)
        if (
            leader_group is None
            or result.leader_actor_id not in leader_group.actor_ids
            or not set(result.follower_actor_ids) < set(leader_group.actor_ids)
            or result.group_size != len(result.follower_actor_ids) + 1
        ):
            raise ValidationError("Leadership activity disagrees with its subgroup")
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
    if len({value.id for value in rules.leadership}) != len(rules.leadership):
        raise ValidationError("Duplicate Leadership activity rule")
    if len({value.trigger_id for value in rules.leadership}) != len(rules.leadership):
        raise ValidationError("Duplicate Leadership trigger")
    if any(
        len(set(value.follower_actor_ids)) != len(value.follower_actor_ids)
        for value in rules.leadership
    ):
        raise ValidationError("Duplicate Leadership follower")
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
