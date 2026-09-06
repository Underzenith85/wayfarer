"""Finite declarative objectives: no generated code or expression evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.simulation.resources import Id, Record

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState

Outcome = Literal["ongoing", "success", "partial-success", "failure", "abandoned"]


class Predicate(Record):
    kind: Literal["fact", "known", "item", "condition", "location", "time", "event", "custody"]
    subject_id: Id
    value: str = Field(max_length=500)
    minimum: int = Field(default=1, ge=0)
    negate: bool = False

    def evaluate(self, state: PlayState) -> bool:
        result = False
        if self.kind == "fact":
            result = any(
                f.id == self.subject_id and f.value == self.value for f in state.world.facts
            )
        elif self.kind == "known":
            result = (self.subject_id, self.value) in state.world.knowledge
        elif self.kind == "item":
            result = (
                sum(
                    i.quantity
                    for i in state.resources.items
                    if i.owner_id == self.subject_id and i.definition_id == self.value
                )
                >= self.minimum
            )
        elif self.kind == "condition":
            result = any(
                a.actor_id == self.subject_id and self.value in a.conditions for a in state.actors
            )
        elif self.kind == "location":
            result = any(
                e.id == self.subject_id and e.location_id == self.value
                for e in state.world.entities
            )
        elif self.kind == "custody":
            result = any(
                e.id == self.subject_id and (e.owner_id or "") == self.value
                for e in state.world.entities
            )
        elif self.kind == "time":
            result = state.resources.game_time >= self.minimum
        elif self.kind == "event":
            result = any(
                e.id == self.subject_id and e.kind == self.value for e in state.resources.events
            )
        return not result if self.negate else result


class Objective(Record):
    id: Id
    title: str = Field(min_length=1, max_length=200)
    required: bool = True
    visible_to: tuple[Id, ...] = ()
    predicates: tuple[Predicate, ...] = Field(min_length=1, max_length=30)


class Reward(Record):
    id: Id
    actor_id: Id
    points: int = Field(default=0, ge=0, le=10000)
    # Rewards transfer an existing escrow item; they never mint arbitrary items.
    item_id: Id | None = None
    outcomes: tuple[Literal["success", "partial-success"], ...] = ("success",)


class ObjectiveRules(Record):
    id: Id
    version: int = Field(ge=1)
    objectives: tuple[Objective, ...] = Field(min_length=1, max_length=100)
    failures: tuple[Predicate, ...] = ()
    deadline: int | None = Field(default=None, ge=1)
    partial_on_deadline: bool = True
    rewards: tuple[Reward, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> ObjectiveRules:
        if len({o.id for o in self.objectives}) != len(self.objectives):
            raise ValueError("Duplicate objective")
        if not any(o.required for o in self.objectives):
            raise ValueError("At least one required objective is needed")
        if len({r.id for r in self.rewards}) != len(self.rewards):
            raise ValueError("Duplicate reward")
        for objective in self.objectives:
            keys = {
                (p.kind, p.subject_id, p.value, p.minimum): p.negate for p in objective.predicates
            }
            if len(keys) != len(objective.predicates):
                raise ValueError("Duplicate or contradictory objective predicates")
            if any(p in self.failures for p in objective.predicates):
                raise ValueError("Required progress contradicts failure predicate")
        return self

    def validate_state(self, state: PlayState, equipment_ids: frozenset[str]) -> None:
        entities = {e.id for e in state.world.entities}
        actors = {a.actor_id for a in state.actors}
        facts = {f.id for f in state.world.facts}
        for objective in self.objectives:
            if not set(objective.visible_to) <= actors:
                raise ValidationError("Unknown objective audience")
        for p in (*self.failures, *(p for o in self.objectives for p in o.predicates)):
            if p.kind == "fact" and p.subject_id not in facts:
                raise ValidationError("Unknown objective fact")
            if (
                p.kind in ("known", "item", "condition", "location", "custody")
                and p.subject_id not in entities
            ):
                raise ValidationError("Unknown objective subject")
            if p.kind == "known" and p.value not in facts:
                raise ValidationError("Unknown objective knowledge")
            if p.kind == "item" and p.value not in equipment_ids:
                raise ValidationError("Unknown objective inventory definition")
            if p.kind == "location" and p.value not in entities:
                raise ValidationError("Unknown objective location")
            if p.kind == "custody" and p.value and p.value not in entities:
                raise ValidationError("Unknown custodian")
            if p.kind == "condition" and p.value not in ("unconscious", "stunned", "restrained"):
                raise ValidationError("Unsupported objective condition")
            if p.kind == "event" and p.subject_id not in {e.id for e in state.resources.events} | {
                e.id for e in state.resources.scheduled
            }:
                raise ValidationError("Unknown objective event")
        items = {i.id for i in state.resources.items}
        if any(
            r.actor_id not in actors or (r.item_id is not None and r.item_id not in items)
            for r in self.rewards
            if r.id not in state.objectives.settled_reward_ids
        ):
            raise ValidationError("Unresolved reward actor or escrow item")


class ObjectiveEvidence(Record):
    objective_id: Id
    satisfied: bool
    predicates: tuple[bool, ...]
    visible_to: tuple[Id, ...] = ()


class ObjectiveState(Record):
    outcome: Outcome = "ongoing"
    evidence: tuple[ObjectiveEvidence, ...] = ()
    revision: int = Field(default=0, ge=0)
    at: int = Field(default=0, ge=0)
    rules_version: int = Field(default=0, ge=0)
    settled_reward_ids: tuple[Id, ...] = ()


def evaluate(state: PlayState, rules: ObjectiveRules, *, abandon: bool = False) -> ObjectiveState:
    if state.objectives.outcome != "ongoing":
        return state.objectives
    evidence = tuple(
        ObjectiveEvidence(
            objective_id=o.id,
            visible_to=o.visible_to,
            satisfied=all(p.evaluate(state) for p in o.predicates),
            predicates=tuple(p.evaluate(state) for p in o.predicates),
        )
        for o in rules.objectives
    )
    satisfied = {e.objective_id for e in evidence if e.satisfied}
    required = {o.id for o in rules.objectives if o.required}
    outcome: Outcome = "ongoing"
    # Declared order: failure > deadline (exclusive success boundary) > abandon > success.
    if any(p.evaluate(state) for p in rules.failures):
        outcome = "failure"
    elif rules.deadline is not None and state.resources.game_time >= rules.deadline:
        outcome = "partial-success" if rules.partial_on_deadline and satisfied else "failure"
    elif abandon:
        outcome = "abandoned"
    elif required <= satisfied:
        outcome = "success"
    return ObjectiveState(
        outcome=outcome,
        evidence=evidence,
        revision=state.revision,
        at=state.resources.game_time,
        rules_version=rules.version,
    )
