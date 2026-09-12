"""Invariants, replay and negative paths for profile-selected GURPS checks."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Modifier, ModifierKind, Outcome, RecordedDice, evaluate_success
from wayfarer.rules.gurps_checks import (
    Contestant,
    RepeatedAttemptPolicy,
    quick_contest,
    regular_contest,
    regular_contest_adjustment,
    repeated_attempt,
    replay_quick_contest,
    replay_regular_contest,
    replay_resistance,
    replay_success,
    resistance_roll,
    rule_of_16_modifier,
    success_roll,
)

BASIC = "gurps-basic-set-4e-2004"
LITE = "gurps-lite-4e-2004"


class Forbidden:
    """A live random source that must never be consulted during replay."""

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        raise AssertionError("replay must not roll")


dice = st.tuples(*(st.integers(1, 6),) * 3)
targets = st.integers(min_value=-5, max_value=30)


@given(targets, dice)
def test_success_outcome_agrees_with_margin_and_boundaries(
    target: int, roll: tuple[int, int, int]
) -> None:
    trace = success_roll(LITE, target, rng=RecordedDice(roll))
    total = sum(roll)
    assert trace.margin == target - total
    succeeded = total <= 4 or (total <= target and total <= 16)
    assert trace.outcome.succeeded == succeeded
    assert (trace.outcome is Outcome.CRITICAL_SUCCESS) == (
        total <= 4 or (total == 5 and target >= 15) or (total == 6 and target >= 16)
    )
    assert (trace.outcome is Outcome.CRITICAL_FAILURE) == (
        total == 18 or (total == 17 and target <= 15) or total - target >= 10
    )
    assert replay_success(trace) == trace


@given(targets, targets, dice, dice)
def test_quick_contest_is_antisymmetric_and_margin_driven(
    first: int, second: int, roll_a: tuple[int, int, int], roll_b: tuple[int, int, int]
) -> None:
    forward = quick_contest(
        BASIC, Contestant("a", first), Contestant("b", second), rng=RecordedDice(roll_a + roll_b)
    )
    backward = quick_contest(
        BASIC, Contestant("b", second), Contestant("a", first), rng=RecordedDice(roll_b + roll_a)
    )
    assert forward.winner == backward.winner
    assert forward.victory_margin == backward.victory_margin
    a, b = forward.first, forward.second
    if a.outcome.succeeded != b.outcome.succeeded:
        assert forward.winner == ("a" if a.outcome.succeeded else "b")
    elif a.margin == b.margin:
        assert forward.winner is None and forward.decision == "tie"
    else:
        assert forward.winner == ("a" if a.margin > b.margin else "b")
        assert forward.victory_margin == abs(a.margin - b.margin)
    assert replay_quick_contest(forward) == forward


@given(st.integers(-5, 30), st.integers(-5, 30))
def test_regular_contest_levelling_only_moves_both_extremes(first: int, second: int) -> None:
    adjustment = regular_contest_adjustment(Contestant("a", first), Contestant("b", second))
    high, low = max(first, second), min(first, second)
    if low > 14:
        assert high + adjustment == 14 and adjustment < 0
    elif high < 6:
        assert low + adjustment == 6 and adjustment > 0
    else:
        assert adjustment == 0


@settings(max_examples=60)
@given(
    st.integers(3, 25),
    st.integers(3, 25),
    st.lists(st.integers(1, 6), min_size=6, max_size=120),
)
def test_regular_contest_ends_in_the_first_split_round(
    first: int, second: int, rolls: list[int]
) -> None:
    rounds = len(rolls) // 6
    try:
        trace = regular_contest(
            BASIC,
            Contestant("a", first),
            Contestant("b", second),
            rng=RecordedDice(rolls),
            round_limit=rounds,
        )
    except ValidationError as error:
        assert "undecided" in str(error)
        return
    for a, b in trace.rounds[:-1]:
        assert a.outcome.succeeded == b.outcome.succeeded
    final_a, final_b = trace.rounds[-1]
    assert final_a.outcome.succeeded != final_b.outcome.succeeded
    assert trace.winner == ("a" if final_a.outcome.succeeded else "b")
    assert replay_regular_contest(trace) == trace


@given(st.integers(3, 30), st.integers(3, 30), dice, dice)
def test_rule_of_16_caps_attacker_at_higher_of_16_and_resistance(
    attacker: int, resister: int, roll_a: tuple[int, int, int], roll_b: tuple[int, int, int]
) -> None:
    trace = resistance_roll(
        BASIC,
        Contestant("caster", attacker),
        Contestant("victim", resister),
        rule_of_16=True,
        rng=RecordedDice(roll_a + roll_b),
    )
    assert trace.attacker.effective_target == min(attacker, max(16, resister))
    assert trace.resister.effective_target == resister
    assert trace.affected == (trace.contest.winner == "caster")
    assert not (trace.contest.decision == "tie" and trace.affected)
    assert replay_resistance(trace) == trace


def test_rule_of_16_modifier_is_typed_and_only_negative() -> None:
    assert rule_of_16_modifier(BASIC, Contestant("c", 16), Contestant("v", 10)) == ()
    (cap,) = rule_of_16_modifier(BASIC, Contestant("c", 20), Contestant("v", 10))
    assert cap.kind is ModifierKind.RULE_OF_16 and cap.value == -4
    (cap,) = rule_of_16_modifier(
        BASIC,
        Contestant("c", 15, (Modifier(5, "power", "gm", "1", ModifierKind.TRAIT),)),
        Contestant("v", 12, (Modifier(5, "resistant", "gm", "1", ModifierKind.TRAIT),)),
    )
    assert cap.value == -3


def test_replay_never_consults_live_randomness() -> None:
    contest = quick_contest(
        BASIC, Contestant("a", 12), Contestant("b", 10), rng=RecordedDice([2, 3, 4, 4, 4, 4])
    )
    assert replay_quick_contest(contest) == contest
    with pytest.raises(AssertionError, match="must not roll"):
        quick_contest(BASIC, Contestant("a", 12), Contestant("b", 10), rng=Forbidden())
    recorded = RecordedDice([6, 6])
    with pytest.raises(ValidationError, match="more dice than were recorded"):
        success_roll(LITE, 10, rng=recorded)
    with pytest.raises(ValidationError, match="outside 1-6"):
        RecordedDice([7, 1, 1]).randbelow(6)
    with pytest.raises(ValidationError, match="outside 1-6"):
        evaluate_success(10, (), (0, 1, 1), rules_package="p", rules_version="1", rule_id="r")


def test_regular_contest_replay_rejects_tampered_receipts() -> None:
    trace = regular_contest(
        BASIC,
        Contestant("a", 18),
        Contestant("b", 16),
        rng=RecordedDice([3, 3, 3, 3, 3, 3, 5, 5, 5, 4, 4, 4]),
    )
    assert trace.adjustment == -4 and len(trace.rounds) == 2
    tampered = type(trace)(
        trace.profile_id,
        trace.first_id,
        trace.second_id,
        trace.adjustment,
        trace.rounds[:1],
        trace.winner,
    )
    with pytest.raises(ValidationError, match="undecided"):
        replay_regular_contest(tampered)
    with pytest.raises(ValidationError, match="no rounds"):
        replay_regular_contest(type(trace)(BASIC, "a", "b", 0, (), "a"))


def test_profile_and_capability_gates_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        success_roll("package:wayfarer-lite", 10, rng=RecordedDice([3, 3, 3]))
    with pytest.raises(ValidationError, match="outside profile"):
        regular_contest(LITE, Contestant("a", 10), Contestant("b", 10), rng=RecordedDice([]))
    with pytest.raises(ValidationError, match="Rule of 16 is outside profile"):
        resistance_roll(
            LITE, Contestant("a", 10), Contestant("b", 10), rule_of_16=True, rng=RecordedDice([])
        )
    lite = resistance_roll(
        LITE,
        Contestant("a", 20),
        Contestant("b", 10),
        rule_of_16=False,
        rng=RecordedDice([5, 5, 5, 4, 4, 4]),
    )
    assert lite.attacker.effective_target == 20 and lite.affected
    with pytest.raises(ValidationError, match="distinct identifiers"):
        quick_contest(BASIC, Contestant("a", 10), Contestant("a", 10), rng=RecordedDice([]))
    with pytest.raises(ValidationError, match="round limit"):
        regular_contest(
            BASIC, Contestant("a", 10), Contestant("b", 10), rng=RecordedDice([]), round_limit=0
        )


def test_repeated_attempt_policies_and_history_validation() -> None:
    first = repeated_attempt(
        BASIC, RepeatedAttemptPolicy.RETRY_UNTIL_SUCCESS, (), 10, rng=RecordedDice([3, 3, 3])
    )
    assert first.attempt == 1 and first.revealed and not first.hazard
    with pytest.raises(ValidationError, match="already succeeded"):
        repeated_attempt(
            BASIC, RepeatedAttemptPolicy.RETRY_UNTIL_SUCCESS, (first,), 10, rng=RecordedDice([])
        )
    with pytest.raises(ValidationError, match="share one profile and policy"):
        repeated_attempt(
            BASIC, RepeatedAttemptPolicy.HAZARDOUS_FAILURE, (first,), 10, rng=RecordedDice([])
        )
    with pytest.raises(ValidationError, match="share one profile and policy"):
        repeated_attempt(
            LITE, RepeatedAttemptPolicy.RETRY_UNTIL_SUCCESS, (first,), 10, rng=RecordedDice([])
        )
    penalised = repeated_attempt(
        LITE,
        RepeatedAttemptPolicy.HAZARDOUS_FAILURE,
        (),
        10,
        (Modifier(-2, "second try", "gm", "1", ModifierKind.REPEATED_ATTEMPT),),
        rng=RecordedDice([3, 3, 4]),
    )
    assert penalised.check.effective_target == 8 and penalised.hazard
    assert penalised.check.modifiers[0].kind is ModifierKind.REPEATED_ATTEMPT


def test_traces_name_profile_baseline_and_capability() -> None:
    trace = success_roll(
        BASIC,
        11,
        (Modifier(1, "lantern", "gear", "1", ModifierKind.EQUIPMENT),),
        rng=RecordedDice([2, 2, 2]),
    )
    assert trace.rules_package == BASIC
    assert trace.rules_version == "gurps-4e-characters-3p-2008+campaigns-4p-2008"
    assert trace.rule_id == "gurps.check.success" and trace.effective_target == 12
    resisted = resistance_roll(
        BASIC,
        Contestant("c", 20),
        Contestant("v", 10),
        rule_of_16=True,
        rng=RecordedDice([4, 4, 4, 3, 3, 3]),
    )
    assert resisted.attacker.rule_id == "gurps.check.resistance"
    assert [m.kind.value for m in resisted.attacker.modifiers] == ["rule-of-16"]
