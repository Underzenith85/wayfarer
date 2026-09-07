"""Subgroup commands and conservative shared-time scheduling under one campaign lock."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import SceneService, TravelScene
from wayfarer.simulation.actions import ActionCommand, Inspect, PlayState, Social, UseItem, Wait
from wayfarer.simulation.party import (
    ActivityReceipt,
    PendingEffect,
    QueuedActivity,
    Subgroup,
    group_for,
    migrate,
)
from wayfarer.simulation.resources import Advance, Id, Transfer


class PartyCommand(ActionCommand):
    kind: Literal[
        "split_party",
        "rejoin_party",
        "queue_activity",
        "pause_group",
        "resume_group",
        "signal_scene",
        "transfer_item",
    ]
    target_id: Id | None = None
    activity_json: str | None = Field(default=None, max_length=10000)


class PartyService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def _duration(
        self, state: PlayState, command: PartyCommand
    ) -> tuple[int, str, Literal["action", "noncombat", "scene"]]:
        if command.activity_json is None:
            raise ValidationError("Queued activity is required")
        try:
            value: object = json.loads(command.activity_json)
        except ValueError as exc:
            raise ValidationError("Invalid activity JSON") from exc
        if isinstance(value, dict) and value.get("kind") == "travel_scene":
            try:
                travel = TravelScene.model_validate_json(command.activity_json)
            except ValueError as exc:
                raise ValidationError("Invalid queued travel") from exc
            group = group_for(state, command.actor_id)
            scenes = self.play.engine.rules.scenes
            if scenes is None or len(group.actor_ids) != 1:
                raise ValidationError("Split before individual travel")
            scene = next(s for s in scenes.scenes if s.id == group.scene_id)
            route = next((e for e in scene.exits if e.id == travel.exit_id), None)
            if (
                route is None
                or travel.actor_id != command.actor_id
                or travel.hypothetical
                or travel.expected_revision != command.expected_revision
            ):
                raise ValidationError("Invalid travel authority, revision or route")
            return (
                route.ticks,
                travel.model_copy(update={"id": f"{command.id}:resolve"}).model_dump_json(),
                "scene",
            )
        try:
            action = self.play.propose(value)
        except ValidationError:
            try:
                encounter_action = NoncombatCommand.model_validate_json(command.activity_json)
            except ValueError as exc:
                raise ValidationError("Unsupported queued activity") from exc
            if encounter_action.kind != "approach_noncombat":
                raise ValidationError("Only chosen approaches can be scheduled") from None
            old = next(
                (
                    e
                    for e in state.noncombat
                    if e.id == encounter_action.encounter_id and e.actor_id == command.actor_id
                ),
                None,
            )
            rules = self.play.engine.rules.noncombat
            if old is None or rules is None or old.status != "choice":
                raise ValidationError("No pending noncombat choice") from None
            rule = next(r for r in rules.encounters if r.id == old.rule_id)
            approach = next(
                (a for a in rule.approaches if a.id == encounter_action.selection_id), None
            )
            if approach is None:
                raise ValidationError("Unsupported approach") from None
            duration = next(
                r.duration for r in self.play.engine.rules.checks if r.id == approach.check_rule_id
            )
            typed: ActionCommand = encounter_action
            family: Literal["action", "noncombat", "scene"] = "noncombat"
        else:
            typed, family = action, "action"
            if isinstance(action, Wait):
                duration = action.ticks
            elif isinstance(action, (Inspect, Social)):
                rule_check = self.play.engine.checks.get((action.kind, action.target_id or ""))
                if rule_check is None or (
                    isinstance(action, Social) and action.approach != "diplomacy"
                ):
                    raise ValidationError("Unsupported queued check")
                duration = rule_check.duration
            elif isinstance(action, UseItem):
                duration = self.play.engine.rules.item_ticks
            else:
                raise ValidationError("Only bounded checks, item use and waits can be queued")
            feasible = self.play.engine.assess(state, action)
            if feasible.status != "feasible":
                raise ValidationError("Queued action is not feasible")
        if (
            typed.actor_id != command.actor_id
            or typed.hypothetical
            or typed.expected_revision != command.expected_revision
        ):
            raise ValidationError("Queued activity has invalid authority or revision")
        # Bind the inner identity to the outer durable command; never reuse a client subcommand ID.
        return (
            duration,
            typed.model_copy(update={"id": f"{command.id}:resolve"}).model_dump_json(),
            family,
        )

    def flush(self, state: PlayState) -> PlayState:
        """Schedules first, cross-scene effects second, activities by group/id last."""
        if not state.party.groups:
            return state
        frontier = min(g.ready_through for g in state.party.groups)
        from wayfarer.orchestration.npcs import due_times

        times = sorted(
            {q.due for q in state.party.queue if q.due <= frontier}
            | {e.due for e in state.party.effects if e.due <= frontier}
            | {
                e.due
                for e in state.resources.scheduled
                if e.id not in state.resources.fired and e.due <= frontier
            }
            | due_times(self.play, state, frontier)
            | {frontier}
        )
        objective_rules = self.play.engine.rules.objectives
        if (
            objective_rules is not None
            and objective_rules.deadline is not None
            and state.resources.game_time < objective_rules.deadline <= frontier
        ):
            times = sorted(set(times) | {objective_rules.deadline})
        for at in times:
            if at < state.resources.game_time:
                raise ValidationError("Scheduled effect precedes committed time")
            resources = self.play.engine.resources.apply(
                state.resources,
                Advance(
                    id=f"party:{state.revision}:{at}:time",
                    actor_id=state.party.groups[0].actor_ids[0],
                    expected_revision=state.resources.revision,
                    to=at,
                ),
                system=True,
            )
            state = state.model_copy(
                update={"resources": resources.model_copy(update={"revision": state.revision})}
            )
            state = self.play.checkpoint(state, run_npcs=False)
            for effect in sorted(
                (e for e in state.party.effects if e.due == at), key=lambda e: e.id
            ):
                world = state.world
                for actor_id in effect.recipient_actor_ids:
                    world = world.learn(actor_id, effect.fact_id)
                state = state.model_copy(
                    update={
                        "world": world,
                        "party": state.party.model_copy(
                            update={
                                "effects": tuple(
                                    e for e in state.party.effects if e.id != effect.id
                                ),
                                "fired_effect_ids": state.party.fired_effect_ids + (effect.id,),
                            }
                        ),
                    }
                )
            activities = sorted(
                (q for q in state.party.queue if q.due == at), key=lambda q: (q.group_id, q.id)
            )
            for activity in activities:
                revision = state.revision
                state = state.model_copy(
                    update={
                        "party": state.party.model_copy(
                            update={
                                "queue": tuple(q for q in state.party.queue if q.id != activity.id)
                            }
                        )
                    }
                )
                before = state
                # Pure reducers normally add one revision. All due work here belongs to this transaction.
                working = state.model_copy(
                    update={
                        "revision": revision - 1,
                        "resources": state.resources.model_copy(update={"revision": revision - 1}),
                    }
                )
                status: Literal["committed", "rejected"] = "committed"
                code = "activity.resolved"
                try:
                    if activity.family == "action":
                        from wayfarer.simulation.actions import ACTION_ADAPTER

                        action = ACTION_ADAPTER.validate_json(activity.command_json).model_copy(
                            update={"expected_revision": revision - 1}
                        )
                        # Existing ledger entries may belong to this same atomic checkpoint.
                        working = working.model_copy(
                            update={"revision": revision, "resources": state.resources}
                        )
                        action = action.model_copy(update={"expected_revision": revision})
                        state, result = self.play.engine.resolve(
                            working, action, rng=self.play.rng, advance_time=False
                        )
                        if result.status != "committed":
                            raise ValidationError("Activity became infeasible")
                        state = state.model_copy(
                            update={
                                "revision": revision,
                                "resources": state.resources.model_copy(
                                    update={"revision": revision}
                                ),
                                "last_result": result.model_copy(update={"revision": revision}),
                            }
                        )
                    elif activity.family == "recovery":
                        from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService

                        state = RecoveryService(self.play).finish(
                            before, RecoveryCommand.model_validate_json(activity.command_json)
                        )
                    elif activity.family == "scene":
                        scene_command = TravelScene.model_validate_json(activity.command_json)
                        state = SceneService(self.play).reduce(
                            working, scene_command, advance_time=False
                        )
                    else:
                        action_nc = NoncombatCommand.model_validate_json(
                            activity.command_json
                        ).model_copy(update={"expected_revision": revision - 1})
                        state = NoncombatService(self.play).reduce(
                            working, action_nc, advance_time=False
                        )
                except (ValidationError, ConflictError):
                    state, status, code = before, "rejected", "activity.no_longer_feasible"
                receipt = ActivityReceipt(
                    id=activity.id, actor_id=activity.actor_id, at=at, status=status, code=code
                )
                state = state.model_copy(
                    update={
                        "party": state.party.model_copy(
                            update={"receipts": state.party.receipts + (receipt,)}
                        )
                    }
                )
                state = self.play.checkpoint(state, run_npcs=False)
            state = self.play.checkpoint(state)
        return state

    def reduce(self, state: PlayState, command: PartyCommand) -> PlayState:
        from wayfarer.orchestration.recovery import guard

        guard(state, command.actor_id, command.kind)
        state = migrate(state)
        group = group_for(state, command.actor_id)
        groups = state.party.groups
        if command.kind not in ("pause_group", "resume_group") and group.paused:
            raise ConflictError("Subgroup is explicitly paused")
        in_combat = any(
            e.status == "active" and set(e.turn_order) & set(group.actor_ids)
            for e in state.encounters
        )
        if command.kind == "queue_activity":
            if in_combat:
                raise ConflictError("Combat advances its own subgroup clock")
            duration, body, family = self._duration(state, command)
            activity = QueuedActivity(
                id=command.id,
                group_id=group.id,
                actor_id=command.actor_id,
                generation=group.generation,
                start=group.ready_through,
                due=group.ready_through + duration,
                command_json=body,
                family=family,
            )
            if any(q.group_id == group.id for q in state.party.queue):
                raise ConflictError("Subgroup already has unresolved activity")
            group = group.model_copy(update={"ready_through": activity.due})
            state = state.model_copy(
                update={
                    "party": state.party.model_copy(
                        update={"queue": state.party.queue + (activity,)}
                    )
                }
            )
        elif command.kind in ("pause_group", "resume_group"):
            if command.target_id is not None or command.activity_json is not None:
                raise ValidationError("Pause policy accepts no extra fields")
            group = group.model_copy(update={"paused": command.kind == "pause_group"})
        elif command.kind == "signal_scene":
            rules = self.play.engine.rules.party
            effect = (
                next((e for e in rules.effects if e.id == command.target_id), None)
                if rules
                else None
            )
            if effect is None or effect.source_scene_id != group.scene_id:
                raise ValidationError("Unknown authored cross-scene effect")
            if effect.id in state.party.fired_effect_ids or any(
                e.id == effect.id for e in state.party.effects
            ):
                raise ConflictError("Cross-scene effect already emitted")
            state = state.model_copy(
                update={
                    "party": state.party.model_copy(
                        update={
                            "effects": state.party.effects
                            + (
                                PendingEffect(
                                    id=effect.id,
                                    due=group.ready_through + effect.delay,
                                    fact_id=effect.fact_id,
                                    recipient_actor_ids=effect.recipient_actor_ids,
                                ),
                            )
                        }
                    )
                }
            )
        elif command.kind == "transfer_item":
            if command.activity_json is None:
                raise ValidationError("Transfer payload required")
            try:
                transfer = Transfer.model_validate_json(command.activity_json)
            except ValueError as exc:
                raise ValidationError("Invalid inventory transfer") from exc
            transfer_group = group_for(state, transfer.owner_id)
            if (
                transfer.actor_id != command.actor_id
                or transfer_group.scene_id != group.scene_id
                or group.ready_through != transfer_group.ready_through
                or group.ready_through != state.resources.game_time
                or in_combat
                or state.party.queue
            ):
                raise ValidationError("Remote or unsynchronized item transfer")
            resources = self.play.engine.resources.apply(
                state.resources,
                transfer.model_copy(
                    update={
                        "id": f"{command.id}:transfer",
                        "expected_revision": state.resources.revision,
                    }
                ),
            )
            state = state.model_copy(update={"resources": resources})
        else:
            if (
                in_combat
                or state.party.queue
                or state.party.effects
                or any(g.ready_through != state.resources.game_time for g in groups)
            ):
                raise ConflictError("Split/rejoin requires a synchronization barrier")
            if command.activity_json is not None or command.target_id is None:
                raise ValidationError("Split/rejoin requires only a target subgroup ID")
            if command.kind == "split_party":
                if len(group.actor_ids) < 2 or any(g.id == command.target_id for g in groups):
                    raise ValidationError("Cannot split singleton or reuse subgroup ID")
                new = Subgroup(
                    id=command.target_id,
                    scene_id=group.scene_id,
                    actor_ids=(command.actor_id,),
                    ready_through=state.resources.game_time,
                )
                group = group.model_copy(
                    update={
                        "actor_ids": tuple(a for a in group.actor_ids if a != command.actor_id),
                        "generation": group.generation + 1,
                    }
                )
                groups += (new,)
            else:
                target = next((g for g in groups if g.id == command.target_id), None)
                if (
                    target is None
                    or target.id == group.id
                    or target.scene_id != group.scene_id
                    or target.paused
                ):
                    raise ValidationError("Rejoin requires a reachable synchronized subgroup")
                # Only the requesting player's actor moves; other controllers retain their actors.
                target = target.model_copy(
                    update={
                        "actor_ids": target.actor_ids + (command.actor_id,),
                        "generation": target.generation + 1,
                    }
                )
                groups = tuple(target if g.id == target.id else g for g in groups)
                remaining = tuple(a for a in group.actor_ids if a != command.actor_id)
                if remaining:
                    group = group.model_copy(
                        update={"actor_ids": remaining, "generation": group.generation + 1}
                    )
                else:
                    groups = tuple(g for g in groups if g.id != group.id)
        groups = tuple(group if g.id == group.id else g for g in groups)
        revision = state.revision + 1
        state = state.model_copy(
            update={
                "revision": revision,
                "resources": state.resources.model_copy(update={"revision": revision}),
                "party": state.party.model_copy(update={"groups": groups}),
            }
        )
        return self.flush(state)

    async def execute(self, cid: str, value: object, *, authenticated_actor_id: str) -> PlayState:
        try:
            command = PartyCommand.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid subgroup command") from exc
        if command.actor_id != authenticated_actor_id or command.hypothetical:
            raise ValidationError("Subgroup command is not authorized")
        payload = json.dumps(
            {"operation": "party", "command": command.model_dump(mode="json")}, sort_keys=True
        )

        def resolve(campaign: Campaign) -> Event:
            state = self.reduce(self.play._load(campaign), command)
            self.play.engine.validate(state)
            campaign["revision"], campaign["play_json"] = state.revision, state.model_dump_json()
            return Event(input=payload, action="party", outcome=command.kind, roll=None)

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return self.play._load(committed["state"])
