"""Stable-order offscreen decisions, committed with the shared-time checkpoint."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.simulation.actions import ActionCommand, PlayState
from wayfarer.simulation.npcs import NPCDecision, NPCProgress
from wayfarer.simulation.resources import Consume

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class NPCProposal(ActionCommand):
    kind: Literal["propose_npc"] = "propose_npc"
    plan_id: str
    action_id: str


def initialize(play: PlayService, state: PlayState) -> PlayState:
    rules = play.engine.rules.npcs
    if rules is None or state.npcs.progress:
        return state
    progress = tuple(NPCProgress(plan_id=p.id, next_due=p.first_due) for p in rules.plans)
    return state.model_copy(
        update={
            "npcs": state.npcs.model_copy(
                update={
                    "progress": progress,
                    "decisions": tuple(
                        NPCDecision(
                            id=f"npc:{p.id}:0", plan_id=p.id, due=p.first_due, status="pending"
                        )
                        for p in rules.plans
                    ),
                }
            )
        }
    )


def due_times(play: PlayService, state: PlayState, frontier: int) -> set[int]:
    rules = play.engine.rules.npcs
    if rules is None:
        return set()
    plans = {p.id: p for p in rules.plans}
    times: set[int] = set()
    for progress in state.npcs.progress:
        plan = plans[progress.plan_id]
        remaining = min(
            plan.action_budget - progress.spent_actions, plan.clock_limit - progress.clock
        )
        times.update(
            progress.next_due + i * plan.interval
            for i in range(remaining)
            if progress.next_due + i * plan.interval <= frontier
        )
    return times


def checkpoint(play: PlayService, state: PlayState) -> PlayState:
    rules = play.engine.rules.npcs
    if rules is None:
        return state
    state = initialize(play, state)
    plans = {p.id: p for p in rules.plans}
    for _ in range(rules.checkpoint_budget):
        eligible = sorted(
            (
                p
                for p in state.npcs.progress
                if p.next_due <= state.resources.game_time
                and p.spent_actions < plans[p.plan_id].action_budget
                and p.clock < plans[p.plan_id].clock_limit
            ),
            key=lambda p: (p.next_due, p.plan_id),
        )
        if not eligible:
            break
        progress = eligible[0]
        plan = plans[progress.plan_id]
        known = frozenset(f.id for f in state.world.perspective(plan.actor_id).facts)
        options = tuple(a for a in plan.actions if set(a.required_fact_ids) <= known)
        proposal = next(
            (
                d
                for d in state.npcs.decisions
                if d.plan_id == plan.id and d.due == progress.next_due and d.status == "proposed"
            ),
            None,
        )
        choice = next(
            (a for a in options if proposal and a.id == proposal.action_id),
            options[0] if options else None,
        )
        outcome: Literal["committed", "fallback", "rejected"] = "committed"
        if choice is None:
            outcome = "fallback"
        before = state
        if choice is not None:
            try:
                resources = state.resources
                if choice.cost:
                    item = next(
                        (
                            i
                            for i in resources.items
                            if i.owner_id == plan.actor_id
                            and i.definition_id == choice.cost_definition_id
                            and i.quantity >= choice.cost
                        ),
                        None,
                    )
                    if item is None:
                        raise ValidationError("NPC cannot afford action")
                    resources = play.engine.resources.apply(
                        resources,
                        Consume(
                            id=f"npc:{plan.id}:{progress.spent_actions}:cost",
                            actor_id=plan.actor_id,
                            expected_revision=resources.revision,
                            item_id=item.id,
                            quantity=choice.cost,
                        ),
                    )
                world = state.world
                # All communication content must already be known to the sender.
                if not set(choice.reveal_fact_ids) <= known:
                    raise ValidationError("NPC cannot communicate unknown facts")
                for recipient in choice.recipient_ids:
                    for fact in choice.reveal_fact_ids:
                        world = world.learn(recipient, fact)
                state = state.model_copy(
                    update={
                        "world": world,
                        "resources": resources.model_copy(update={"revision": state.revision}),
                    }
                )
                if choice.kind == "transfer_prisoner":
                    from wayfarer.orchestration.recovery import RecoveryService

                    if choice.setback_rule_id is None or choice.target_actor_id is None:
                        raise ValidationError("Prisoner transfer requires an authored destination")
                    captive = next(
                        (
                            c
                            for c in state.recovery.captivity
                            if c.actor_id == choice.target_actor_id and c.released_at is None
                        ),
                        None,
                    )
                    if captive is None or captive.captor_id != plan.actor_id:
                        raise ValidationError("NPC does not hold this prisoner")
                    state = RecoveryService(play).setback(
                        state,
                        choice.setback_rule_id,
                        choice.target_actor_id,
                        f"npc:{plan.id}:{progress.spent_actions}",
                    )
            except (ValidationError, ConflictError):
                state, outcome = before, "rejected"
        decision = NPCDecision(
            id=f"npc:{plan.id}:{progress.spent_actions}",
            plan_id=plan.id,
            action_id=choice.id if choice else None,
            due=progress.next_due,
            status=outcome,
            known_fact_ids=tuple(sorted(known)),
        )
        updated = progress.model_copy(
            update={
                "next_due": progress.next_due + plan.interval,
                "spent_actions": progress.spent_actions + 1,
                "clock": progress.clock + (outcome == "committed"),
            }
        )
        decisions = tuple(decision if d.id == decision.id else d for d in state.npcs.decisions)
        if updated.spent_actions < plan.action_budget and updated.clock < plan.clock_limit:
            decisions += (
                NPCDecision(
                    id=f"npc:{plan.id}:{updated.spent_actions}",
                    plan_id=plan.id,
                    due=updated.next_due,
                    status="pending",
                ),
            )
        state = state.model_copy(
            update={
                "npcs": state.npcs.model_copy(
                    update={
                        "progress": tuple(
                            updated if p.plan_id == plan.id else p for p in state.npcs.progress
                        ),
                        "decisions": decisions,
                    }
                )
            }
        )
    # Bounded by the finite plan budgets; no recursive reaction/model calls.
    return state


class NPCService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def propose(self, cid: str, value: object, *, authenticated_gm_id: str) -> PlayState:
        command = NPCProposal.model_validate(value)
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("NPC proposals require trusted director authority")
        if command.hypothetical:
            raise ValidationError("Hypothetical proposal cannot be persisted")
        payload = command.model_dump_json()

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            rules = self.play.engine.rules.npcs
            plan = (
                next((p for p in rules.plans if p.id == command.plan_id), None) if rules else None
            )
            progress = next((p for p in state.npcs.progress if p.plan_id == command.plan_id), None)
            if (
                plan is None
                or progress is None
                or command.action_id not in {a.id for a in plan.actions}
            ):
                raise ValidationError("Unknown bounded NPC option")
            if any(
                d.plan_id == plan.id and d.due == progress.next_due and d.status == "proposed"
                for d in state.npcs.decisions
            ):
                raise ValidationError("NPC already has a pending proposal")
            revision = state.revision + 1
            decision = NPCDecision(
                id=command.id,
                plan_id=plan.id,
                action_id=command.action_id,
                due=progress.next_due,
                status="proposed",
            )
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                    "npcs": state.npcs.model_copy(
                        update={"decisions": state.npcs.decisions + (decision,)}
                    ),
                }
            )
            self.play.engine.validate(state)
            campaign["revision"], campaign["play_json"] = revision, state.model_dump_json()
            return Event(
                input=json.dumps({"command": payload}), action="npc", outcome="proposed", roll=None
            )

        result = await self.play.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return self.play._load(result["state"])
