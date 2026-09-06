"""Deterministic declarative effects and derived-value evaluation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Decimal
from enum import IntEnum, StrEnum
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType

from wayfarer.errors import ValidationError


class OperationOrder(IntEnum):
    REPLACE = 10
    ADD = 20
    MULTIPLY = 30
    ROUND = 40
    CLAMP = 50


class Operation(StrEnum):
    REPLACE = "replace"
    ADD = "add"
    MULTIPLY = "multiply"
    ROUND_HALF_EVEN = "round-half-even"
    ROUND_DOWN = "round-down"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class PredicateOperator(StrEnum):
    EQUALS = "equals"
    CONTAINS = "contains"


@dataclass(frozen=True, slots=True)
class MechanicalTarget:
    id: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Predicate:
    field: str
    operator: PredicateOperator
    value: str

    def matches(self, context: Mapping[str, str | frozenset[str]]) -> bool:
        actual = context.get(self.field)
        if self.operator is PredicateOperator.EQUALS:
            return actual == self.value
        return isinstance(actual, frozenset) and self.value in actual


@dataclass(frozen=True, slots=True)
class Effect:
    id: str
    target: str
    operation: Operation
    value: Decimal
    source_id: str
    source_version: str
    stacking_group: str | None = None
    priority: int = 0
    predicates: tuple[Predicate, ...] = ()
    starts_at: int | None = None
    expires_at: int | None = None

    def active(self, context: Mapping[str, str | frozenset[str]], at: int) -> bool:
        return (
            (self.starts_at is None or self.starts_at <= at)
            and (self.expires_at is None or at < self.expires_at)
            and all(predicate.matches(context) for predicate in self.predicates)
        )


@dataclass(frozen=True, slots=True)
class ExplanationEntry:
    effect_id: str
    source_id: str
    source_version: str
    operation: Operation
    before: Decimal
    operand: Decimal
    after: Decimal


@dataclass(frozen=True, slots=True)
class DerivedValue:
    target: str
    value: Decimal
    explanations: tuple[ExplanationEntry, ...]


_ORDER = MappingProxyType(
    {
        Operation.REPLACE: OperationOrder.REPLACE,
        Operation.ADD: OperationOrder.ADD,
        Operation.MULTIPLY: OperationOrder.MULTIPLY,
        Operation.ROUND_HALF_EVEN: OperationOrder.ROUND,
        Operation.ROUND_DOWN: OperationOrder.ROUND,
        Operation.MINIMUM: OperationOrder.CLAMP,
        Operation.MAXIMUM: OperationOrder.CLAMP,
    }
)


class EffectEvaluator:
    def __init__(self, targets: tuple[MechanicalTarget, ...]) -> None:
        if len({target.id for target in targets}) != len(targets):
            raise ValidationError("Duplicate mechanical target")
        graph = {target.id: set(target.dependencies) for target in targets}
        if any(dep not in graph for target in targets for dep in target.dependencies):
            raise ValidationError("Mechanical target has a missing dependency")
        try:
            self.order = tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise ValidationError("Derived-stat dependency cycle") from exc
        self.targets = frozenset(graph)

    def evaluate(
        self,
        target: str,
        base: Decimal,
        effects: tuple[Effect, ...],
        *,
        context: Mapping[str, str | frozenset[str]],
        at: int,
    ) -> DerivedValue:
        if target not in self.targets:
            raise ValidationError(f"Unknown mechanical target: {target}")
        candidates = [
            effect for effect in effects if effect.target == target and effect.active(context, at)
        ]
        selected: dict[str, Effect] = {}
        ungrouped: list[Effect] = []
        for effect in candidates:
            if effect.stacking_group is None:
                ungrouped.append(effect)
            else:
                current = selected.get(effect.stacking_group)
                rank = (effect.priority, abs(effect.value), effect.id)
                if current is None or rank > (current.priority, abs(current.value), current.id):
                    selected[effect.stacking_group] = effect
        ordered = sorted(
            (*ungrouped, *selected.values()), key=lambda e: (_ORDER[e.operation], -e.priority, e.id)
        )
        value = base
        trace: list[ExplanationEntry] = []
        for effect in ordered:
            before = value
            match effect.operation:
                case Operation.REPLACE:
                    value = effect.value
                case Operation.ADD:
                    value += effect.value
                case Operation.MULTIPLY:
                    value *= effect.value
                case Operation.ROUND_HALF_EVEN:
                    value = value.quantize(effect.value, rounding=ROUND_HALF_EVEN)
                case Operation.ROUND_DOWN:
                    value = value.quantize(effect.value, rounding=ROUND_FLOOR)
                case Operation.MINIMUM:
                    value = max(value, effect.value)
                case Operation.MAXIMUM:
                    value = min(value, effect.value)
            trace.append(
                ExplanationEntry(
                    effect.id,
                    effect.source_id,
                    effect.source_version,
                    effect.operation,
                    before,
                    effect.value,
                    value,
                )
            )
        return DerivedValue(target, value, tuple(trace))
