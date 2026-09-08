"""Finite NPC plans with actor-local evidence and explicit resource budgets."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.rules.social_hooks import Appearance, Recognition, ReputationScope
from wayfarer.simulation.resources import Id, Record


class NPCReputation(Record):
    """One authored reputation; the engine owns its reaction value and recognition."""

    id: Id
    level: int = Field(ge=-4, le=4)
    scope: ReputationScope = "everyone"
    recognition: Recognition = "always"
    classes: tuple[Id, ...] = Field(default=(), max_length=10)
    hidden: bool = False


class NPCSocialStanding(Record):
    """Declared standing and observer, not an invented modifier: values follow rules.

    Status, Charisma and Voice are absent on purpose: dispatch derives those from
    the initiator's approved build, and declaring them here would double-count.
    The audience fields describe the reacting party for both sources.
    """

    appearance: Appearance = "average"
    reputations: tuple[NPCReputation, ...] = Field(default=(), max_length=5)
    audience_perceptible: bool = True
    audience_audible: bool = True
    audience_recognizes_status: bool = True
    audience_attracted: bool = False
    audience_classes: tuple[Id, ...] = Field(default=(), max_length=10)


class NPCSocialTrigger(Record):
    """Pinned scenario data, not model-supplied roll targets or trait options."""

    kind: Literal["reaction", "influence", "fright", "self-control"]
    subject_id: Id
    modifier: int = Field(default=0, ge=-100, le=100)
    standing: NPCSocialStanding | None = None
    npc_will: int = Field(default=10, ge=1, le=100)
    skill_id: Id = "skill:diplomacy"
    trait_id: Id | None = None
    required_fact_ids: tuple[Id, ...] = ()
    disclosure_fact_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def standing_belongs_to_a_reaction(self) -> Self:
        if self.standing is not None and self.kind not in ("reaction", "influence"):
            raise ValueError("Standing modifies reaction and influence rolls only")
        return self


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


class NPCSocialAction(NPCAction):
    social: NPCSocialTrigger


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


class NPCSocialPlan(NPCPlan):
    actions: tuple[NPCAction | NPCSocialAction, ...] = Field(min_length=1, max_length=20)


class NPCSocialRules(NPCRules):
    """Opt-in v2 authored policy; the frozen v1 scenario schema stays unchanged."""

    version: Literal[2] = 2
    plans: tuple[NPCSocialPlan, ...] = Field(max_length=50)


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
