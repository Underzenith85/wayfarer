from decimal import Decimal

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Modifier, Outcome, contest, success_check
from wayfarer.rules.effects import (
    Effect,
    EffectEvaluator,
    MechanicalTarget,
    Operation,
    Predicate,
    PredicateOperator,
)
from wayfarer.world import (
    Belief,
    Commitment,
    CommitmentKind,
    CommitmentStatus,
    Entity,
    EntityKind,
    Fact,
    KnowledgeRevealed,
    World,
    replay,
)


class Dice:
    def __init__(self, values: tuple[int, ...]) -> None:
        self.values = iter(values)

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        assert exclusive_upper_bound == 6
        return next(self.values) - 1


def effect(
    name: str,
    operation: Operation,
    value: str,
    *,
    stacking_group: str | None = None,
    priority: int = 0,
    predicates: tuple[Predicate, ...] = (),
    starts_at: int | None = None,
    expires_at: int | None = None,
) -> Effect:
    return Effect(
        name,
        "stat:speed",
        operation,
        Decimal(value),
        "source:test",
        "1",
        stacking_group,
        priority,
        predicates,
        starts_at,
        expires_at,
    )


def test_effect_order_stacking_scope_expiration_rounding_and_trace() -> None:
    evaluator = EffectEvaluator((MechanicalTarget("stat:speed"),))
    effects = (
        effect("replace", Operation.REPLACE, "3.4"),
        effect("low", Operation.ADD, "2", stacking_group="bonus", priority=1),
        effect("high", Operation.ADD, "3", stacking_group="bonus", priority=2),
        effect("expired", Operation.ADD, "100", expires_at=10),
        effect(
            "scope",
            Operation.MULTIPLY,
            "1.5",
            predicates=(Predicate("terrain", PredicateOperator.EQUALS, "road"),),
        ),
        effect("round", Operation.ROUND_DOWN, "1"),
    )
    result = evaluator.evaluate(
        "stat:speed", Decimal("9"), effects, context={"terrain": "road"}, at=10
    )
    assert result.value == Decimal("9")
    assert [entry.effect_id for entry in result.explanations] == [
        "replace",
        "high",
        "scope",
        "round",
    ]
    assert all(entry.source_id == "source:test" for entry in result.explanations)


def test_effect_dependency_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        EffectEvaluator((MechanicalTarget("a", ("b",)), MechanicalTarget("b", ("a",))))


@pytest.mark.parametrize(
    ("dice", "target", "outcome"),
    [
        ((1, 1, 2), 3, Outcome.CRITICAL_SUCCESS),
        ((5, 6, 6), 17, Outcome.FAILURE),
        ((6, 6, 6), 20, Outcome.CRITICAL_FAILURE),
        ((3, 3, 3), 10, Outcome.SUCCESS),
    ],
)
def test_success_check_boundaries(
    dice: tuple[int, int, int], target: int, outcome: Outcome
) -> None:
    trace = success_check(
        target,
        (Modifier(-1, "darkness", "source:test", "1"),),
        rng=Dice(dice),
        rules_package="pkg",
        rules_version="1",
    )
    assert trace.outcome is outcome
    assert trace.dice == dice and trace.margin == target - 1 - sum(dice)
    assert trace.modifiers[0].reason == "darkness" and trace.rule_id == "check:success"


def test_unsupported_check_rejected_and_contest_is_deterministic() -> None:
    with pytest.raises(ValidationError, match="Unsupported"):
        success_check(
            10, rng=Dice((3, 3, 3)), rules_package="pkg", rules_version="1", rule_id="magic"
        )
    result = contest(
        "a", 12, "b", 12, rng=Dice((3, 3, 3, 4, 4, 4)), rules_package="pkg", rules_version="1"
    )
    assert result.winner == "a"


def world() -> World:
    return World(
        entities=(
            Entity("actor:a", EntityKind.ACTOR, "A"),
            Entity("actor:b", EntityKind.ACTOR, "B"),
        ),
        facts=(
            Fact("fact:hidden", "actor:b", "is", "spy"),
            Fact("fact:done", "actor:b", "paid", "debt"),
        ),
        knowledge=(("actor:a", "fact:done"),),
        beliefs=(Belief("actor:a", "fact:hidden", "friend"),),
        commitments=(
            Commitment(
                "debt:1",
                CommitmentKind.DEBT,
                "actor:b",
                "actor:a",
                "Repay",
                "fact:hidden",
                "fact:done",
            ),
        ),
    )


def test_perspective_never_leaks_truth_but_retains_false_belief() -> None:
    view = world().perspective("actor:a")
    assert [f.id for f in view.facts] == ["fact:done"]
    assert view.beliefs[0].value == "friend"
    assert view.commitments == ()


def test_learning_reveals_and_completes_commitments_replayably() -> None:
    revealed = world().learn("actor:a", "fact:hidden")
    assert revealed.commitments[0].status is CommitmentStatus.ACTIVE
    completed = revealed.learn("actor:a", "fact:done")
    assert completed.commitments[0].status is CommitmentStatus.COMPLETED
    assert completed == world().learn("actor:a", "fact:hidden").learn("actor:a", "fact:done")
    assert completed == replay(
        world(),
        (
            KnowledgeRevealed("actor:a", "fact:hidden"),
            KnowledgeRevealed("actor:a", "fact:done"),
        ),
    )


def test_invalid_references_rejected() -> None:
    with pytest.raises(ValidationError, match="entity"):
        World(entities=(Entity("a", EntityKind.ACTOR, "A", owner_id="missing"),)).validate()
