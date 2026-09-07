"""Finite NPC plans with actor-local evidence and explicit resource budgets."""

from typing import Literal

from pydantic import Field

from wayfarer.simulation.resources import Id, Record


class NPCAction(Record):
    id: Id
    kind: Literal["patrol", "communicate", "alarm", "reinforce", "transfer_prisoner"]
    required_fact_ids: tuple[Id, ...] = ()
    reveal_fact_ids: tuple[Id, ...] = ()
    recipient_ids: tuple[Id, ...] = ()
    cost_definition_id: Id | None = None
    cost: int = Field(default=0, ge=0, le=1000000)
    setback_rule_id: Id | None = None
    target_actor_id: Id | None = None


class NPCPlan(Record):
    id: Id
    actor_id: Id
    faction_id: Id | None = None
    goal: str = Field(min_length=1, max_length=1000)
    disposition: Literal["hostile", "neutral", "friendly"] = "neutral"
    first_due: int = Field(ge=0)
    interval: int = Field(ge=1, le=10000)
    action_budget: int = Field(default=10, ge=1, le=100)
    clock_limit: int = Field(default=4, ge=1, le=100)
    actions: tuple[NPCAction, ...] = Field(min_length=1, max_length=20)


class NPCRules(Record):
    id: Id
    version: int = Field(ge=1)
    plans: tuple[NPCPlan, ...] = Field(max_length=50)
    checkpoint_budget: int = Field(default=100, ge=1, le=1000)


class NPCProgress(Record):
    plan_id: Id
    next_due: int = Field(ge=0)
    spent_actions: int = Field(default=0, ge=0)
    clock: int = Field(default=0, ge=0)


class NPCDecision(Record):
    id: Id
    plan_id: Id
    action_id: Id | None = None
    due: int
    status: Literal["proposed", "pending", "committed", "fallback", "rejected"]
    known_fact_ids: tuple[Id, ...] = ()


class NPCState(Record):
    version: Literal[1] = 1
    progress: tuple[NPCProgress, ...] = ()
    decisions: tuple[NPCDecision, ...] = ()
