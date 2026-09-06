"""Resolve authored approaches with existing catalog/effects checks under a lock."""

from __future__ import annotations

import json
from typing import Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import Modifier, Outcome, success_check
from wayfarer.simulation.actions import ActionCommand, PlayState
from wayfarer.simulation.noncombat import NoncombatEncounter
from wayfarer.simulation.resources import Advance, Id


class NoncombatCommand(ActionCommand):
    kind: Literal["start_noncombat", "approach_noncombat", "withdraw_noncombat"]
    encounter_id: Id
    selection_id: Id | None = None


class NoncombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def reduce(
        self, state: PlayState, command: NoncombatCommand, *, advance_time: bool = True
    ) -> PlayState:
        rules = self.play.engine.rules.noncombat
        if rules is None:
            raise ValidationError("Noncombat rules are not configured")
        actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
        cursor = next((c for c in state.actor_scenes if c.actor_id == command.actor_id), None)
        if actor is None or cursor is None or actor.approval is None:
            raise ValidationError("Noncombat actor unavailable")
        if any(e.status == "active" and actor.actor_id in e.turn_order for e in state.encounters):
            raise ConflictError("Actor is in combat")
        if (
            actor.conditions
            or next(p.current for p in state.resources.pools if p.id == f"hp:{actor.actor_id}") == 0
        ):
            raise ValidationError("Actor is incapacitated")
        old = next((e for e in state.noncombat if e.id == command.encounter_id), None)
        if command.kind == "start_noncombat":
            if old is not None or any(
                e.actor_id == actor.actor_id and e.status == "choice" for e in state.noncombat
            ):
                raise ConflictError(
                    "Noncombat encounter already exists or actor has a pending choice"
                )
            rule = next((r for r in rules.encounters if r.id == command.selection_id), None)
        else:
            if old is None or old.actor_id != actor.actor_id or old.status != "choice":
                raise ConflictError("No pending encounter for this actor")
            rule = next((r for r in rules.encounters if r.id == old.rule_id), None)
        if rule is None or rule.scene_id != cursor.scene_id:
            raise ValidationError("Encounter rule unavailable in this scene")
        revision = state.revision + 1
        encounter = old or NoncombatEncounter(
            id=command.encounter_id,
            rule_id=rule.id,
            actor_id=actor.actor_id,
            pending_choices=tuple(a.id for a in rule.approaches),
            revision=revision,
        )
        resources, world = state.resources, state.world
        revealed: tuple[str, ...] = ()
        if command.kind == "withdraw_noncombat":
            if command.selection_id is not None:
                raise ValidationError("Withdrawal has no selection")
            encounter = encounter.model_copy(update={"status": "withdrawn", "pending_choices": ()})
            revealed = rule.withdrawal_fact_ids
        elif command.kind == "approach_noncombat":
            approach = next((a for a in rule.approaches if a.id == command.selection_id), None)
            if approach is None:
                raise ValidationError("Unsupported approach")
            check_rule = next(
                r for r in self.play.engine.rules.checks if r.id == approach.check_rule_id
            )
            pools = {p.id: p for p in resources.pools}
            fp = pools[f"fp:{actor.actor_id}"]
            if fp.current < approach.fatigue_cost:
                raise ValidationError("Insufficient fatigue")
            target = next(e for e in world.entities if e.id == check_rule.target_id)
            entity = next(e for e in world.entities if e.id == actor.actor_id)
            if (target.location_id or target.id) != entity.location_id:
                raise ValidationError("Noncombat check target is remote")
            if (
                actor.available_at > resources.game_time
                or check_rule.not_before > resources.game_time
            ):
                raise ConflictError("Noncombat actor or check is not ready")
            if check_rule.required_equipment is not None and not any(
                i.owner_id == actor.actor_id
                and i.definition_id == check_rule.required_equipment
                and i.ready
                for i in resources.items
            ):
                raise ValidationError("Check equipment is required")
            build, _ = self.play.engine.reviewer.activate(
                actor.proposal,
                actor.approval,
                campaign_id=state.campaign_id,
                actor_id=actor.actor_id,
            )
            if check_rule.definition_id not in {p.definition_id for p in build.purchases}:
                raise ValidationError("Approach requires a purchased skill")
            value, _ = self.play.engine._target(state, actor.actor_id, build, check_rule)
            trace = success_check(
                int(value.value),
                (
                    Modifier(
                        check_rule.modifier,
                        check_rule.id,
                        check_rule.definition_id,
                        check_rule.package_version,
                    ),
                ),
                rng=self.play.rng,
                rules_package=check_rule.package_id,
                rules_version=check_rule.package_version,
            )
            passed = trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
            progress = encounter.progress + (
                approach.success_progress if passed else approach.failure_progress
            )
            failures = encounter.failures + (0 if passed else 1)
            status: Literal["choice", "success", "failure", "withdrawn"] = "choice"
            # Exhausted stakes take precedence over progress on the same failed check.
            if failures >= rule.maximum_failures:
                status = "failure"
            elif progress >= rule.required_progress:
                status = "success"
            revealed = approach.success_fact_ids if passed else approach.failure_fact_ids
            if status == "success":
                revealed += rule.completion_fact_ids
            elif status == "failure":
                revealed += rule.defeat_fact_ids
            pools[fp.id] = fp.model_copy(update={"current": fp.current - approach.fatigue_cost})
            hp = pools[f"hp:{actor.actor_id}"]
            pools[hp.id] = hp.model_copy(
                update={"current": max(0, hp.current - (0 if passed else approach.failure_hp_cost))}
            )
            resources = resources.model_copy(update={"pools": tuple(pools.values())})
            if advance_time:
                resources = self.play.engine.resources.apply(
                    resources,
                    Advance(
                        id=f"{command.id}:time",
                        actor_id=actor.actor_id,
                        expected_revision=resources.revision,
                        to=resources.game_time + check_rule.duration,
                    ),
                    system=True,
                )
            encounter = encounter.model_copy(
                update={
                    "status": status,
                    "progress": progress,
                    "failures": failures,
                    "checks": encounter.checks + (trace,),
                    "pending_choices": encounter.pending_choices if status == "choice" else (),
                }
            )
        for fact_id in revealed:
            world = world.learn(actor.actor_id, fact_id)
        encounter = encounter.model_copy(
            update={
                "revision": revision,
                "revealed_fact_ids": tuple(
                    dict.fromkeys((*encounter.revealed_fact_ids, *revealed))
                ),
            }
        )
        return state.model_copy(
            update={
                "revision": revision,
                "world": world,
                "resources": resources.model_copy(update={"revision": revision}),
                "noncombat": tuple(e for e in state.noncombat if e.id != encounter.id)
                + (encounter,),
            }
        )

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> NoncombatEncounter:
        try:
            command = NoncombatCommand.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid noncombat command") from exc
        if command.actor_id != authenticated_actor_id or command.hypothetical:
            raise ValidationError("Noncombat command is not authorized")
        payload = json.dumps(
            {"operation": "noncombat", "command": command.model_dump(mode="json")}, sort_keys=True
        )

        def resolve(campaign: Campaign) -> Event:
            current = self.play._load(campaign)
            if command.kind == "approach_noncombat":
                from wayfarer.simulation.party import synchronous

                synchronous(current, command.actor_id)
            state = self.reduce(current, command)
            if state.party.groups:
                state = state.model_copy(
                    update={
                        "party": state.party.model_copy(
                            update={
                                "groups": tuple(
                                    g.model_copy(
                                        update={
                                            "ready_through": max(
                                                g.ready_through, state.resources.game_time
                                            )
                                        }
                                    )
                                    for g in state.party.groups
                                )
                            }
                        )
                    }
                )
            state = self.play.checkpoint(state, before=current)
            self.play.engine.validate(state)
            result = next(e for e in state.noncombat if e.id == command.encounter_id)
            campaign["revision"], campaign["play_json"] = state.revision, state.model_dump_json()
            return Event(
                input=payload, action="noncombat", outcome=result.model_dump_json(), roll=None
            )

        committed = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return next(
            e for e in self.play._load(committed["state"]).noncombat if e.id == command.encounter_id
        )
