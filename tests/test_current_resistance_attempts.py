"""Literal B348 resistance exceptions preserve actual attempted dice only."""

from dataclasses import replace
from typing import Literal, cast

import pytest

from wayfarer.engine.rules.checks import Modifier, ModifierKind, RecordedDice
from wayfarer.engine.rules.gurps_checks import Contestant, ResistanceTrace, resistance_roll
from wayfarer.engine.rules.resistance_attempts import (
    FailedResistanceAttempt,
    prepare_resistance_attempt,
    replay_resistance_attempt,
    resistance_attempt,
    resolve_resistance_attempt,
    validate_current_resistance_attempt,
)
from wayfarer.errors import ValidationError

PROFILE = "gurps-basic-set-4e-2004"


def test_failed_attack_needs_only_original_three_dice_and_no_subject_attempt() -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    dice = RecordedDice((4, 4, 4))
    trace = resistance_attempt(prepared, rng=dice)
    assert isinstance(trace, FailedResistanceAttempt)
    assert trace.attacker.dice == (4, 4, 4) and not trace.affected and dice.exhausted()
    assert trace.declared_resister == Contestant("b", 10)
    assert not hasattr(trace, "contest") and not hasattr(trace, "resister")
    assert replay_resistance_attempt(trace) == trace


def test_optional_actual_subject_roll_remains_legal_after_failed_attack() -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    trace = resolve_resistance_attempt(prepared, attacker_dice=(4, 4, 4), resister_dice=(6, 6, 5))
    assert isinstance(trace, ResistanceTrace)
    assert trace.contest.winner == "a" and not trace.affected
    assert trace.resister.dice == (6, 6, 5)
    assert replay_resistance_attempt(trace) == trace


def test_successful_attack_requires_real_subject_dice_and_subject_wins_ties() -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 12), Contestant("b", 15), rule_of_16=False
    )
    with pytest.raises(ValidationError, match="actual subject dice"):
        resolve_resistance_attempt(prepared, attacker_dice=(3, 3, 3))
    dice = RecordedDice((3, 3, 3, 4, 4, 4))
    trace = resistance_attempt(prepared, rng=dice)
    assert isinstance(trace, ResistanceTrace) and trace.contest.winner is None
    assert not trace.affected and dice.exhausted()


def test_cap_applies_before_scoring_and_is_retained_without_subject_dice() -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 30), Contestant("b", 10), rule_of_16=True
    )
    trace = resistance_attempt(prepared, rng=RecordedDice((6, 6, 5)))
    assert isinstance(trace, FailedResistanceAttempt)
    assert trace.rule_of_16 and trace.attacker.effective_target == 16
    assert trace.attacker.modifiers[0].value == -14
    assert replay_resistance_attempt(trace) == trace
    with pytest.raises(ValidationError):
        replace(trace, rule_of_16=False)
    with pytest.raises(ValidationError):
        replace(trace, affected=cast(Literal[False], True))


@pytest.mark.parametrize("score", [1, 2, -1])
def test_voluntary_attacker_floor_is_pure_and_does_not_apply_to_mandatory_subject(
    score: int,
) -> None:
    with pytest.raises(ValidationError, match="at least 3"):
        prepare_resistance_attempt(
            PROFILE, Contestant("a", score), Contestant("b", 10), rule_of_16=False
        )
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 12), Contestant("b", score), rule_of_16=False
    )
    dice = RecordedDice((3, 3, 3, 1, 1, 1))
    trace = resistance_attempt(prepared, rng=dice)
    assert isinstance(trace, ResistanceTrace) and trace.resister.effective_target == score
    assert trace.resister.dice == (1, 1, 1) and dice.exhausted()


def test_preparation_and_failure_receipts_reject_forged_cap_and_success() -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 12), Contestant("b", 10), rule_of_16=False
    )
    with pytest.raises(ValidationError, match="preparation changed"):
        resistance_attempt(
            replace(prepared, cap_modifiers=(Modifier(-1, "forged", "test", "1"),)),
            rng=RecordedDice(()),
        )
    successful = resolve_resistance_attempt(
        prepared, attacker_dice=(3, 3, 3), resister_dice=(4, 4, 4)
    )
    with pytest.raises(ValidationError, match="failed resistance"):
        FailedResistanceAttempt(
            PROFILE, prepared.attacker, prepared.resister, successful.attacker, False
        )
    with pytest.raises(ValidationError, match="own Rule"):
        prepare_resistance_attempt(
            PROFILE,
            Contestant("a", 12, (Modifier(-1, "fake", "x", "1", ModifierKind.RULE_OF_16),)),
            Contestant("b", 10),
            rule_of_16=False,
        )


def test_historical_two_roll_api_keeps_optional_subject_and_original_shape() -> None:
    dice = RecordedDice((4, 4, 4, 6, 6, 5))
    trace = resistance_roll(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False, rng=dice
    )
    assert isinstance(trace, ResistanceTrace) and dice.exhausted()
    assert trace.contest.winner == "a" and not trace.affected
    assert replay_resistance_attempt(trace) == trace


def test_current_failure_survives_strict_native_ability_json_without_old_field_churn() -> None:
    from wayfarer.engine.simulation.ability_types import AbilityEvent, AbilityOutcome

    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=True
    )
    trace = resistance_attempt(prepared, rng=RecordedDice((4, 4, 4)))
    old = AbilityEvent(
        actor_id="a",
        ability_id="advantage:mind-reading",
        target_id="b",
        outcome=AbilityOutcome(outcome="resisted"),
    )
    assert "resistance_attempt" not in old.model_dump_json()
    current = old.model_copy(update={"checks": (trace.attacker,), "resistance_attempt": trace})
    current = current.model_copy(update={"failed": True, "resistance_preparation": prepared})
    restored = AbilityEvent.model_validate_json(current.model_dump_json())
    assert restored == current and restored.resistance_attempt == trace


@pytest.mark.parametrize("raw", [(6, 6), (1, 1, 1, 1), (True, 4, 4), (4.0, 4, 4), (0, 6, 6)])
def test_current_actual_dice_are_exactly_three_real_integer_d6(raw: object) -> None:
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    invalid = cast(tuple[int, int, int], raw)
    with pytest.raises(ValidationError, match="three actual integer"):
        resolve_resistance_attempt(prepared, attacker_dice=invalid)
    with pytest.raises(ValidationError, match="three actual integer"):
        resolve_resistance_attempt(prepared, attacker_dice=(4, 4, 4), resister_dice=invalid)


@pytest.mark.parametrize(
    "change", ["checks", "actor", "target", "failed", "critical", "outcome", "damage", "disclosure"]
)
def test_current_native_ability_reader_rejects_a_contradictory_receipt(change: str) -> None:
    import json

    from wayfarer.engine.simulation.ability_types import AbilityEvent, AbilityOutcome

    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    trace = resistance_attempt(prepared, rng=RecordedDice((4, 4, 4)))
    event = AbilityEvent(
        actor_id="a",
        target_id="b",
        ability_id="trait:ability",
        outcome=AbilityOutcome(outcome="resisted"),
        failed=True,
        checks=(trace.attacker,),
        resistance_attempt=trace,
        resistance_preparation=prepared,
    )
    data = event.model_dump(mode="json")
    if change == "checks":
        data["checks"] = []
    elif change in {"actor", "target"}:
        data[change + "_id"] = "different"
    elif change == "outcome":
        data["outcome"] = {"outcome": "active"}
    elif change == "damage":
        data["damage_dice"] = [4]
    elif change == "disclosure":
        data["outcome"] = {"outcome": "resisted", "revealed_fact_ids": ["unearned"]}
    else:
        data["failed" if change == "failed" else "critical_failure"] = change != "failed"
    with pytest.raises(ValidationError, match="contradicts"):
        AbilityEvent.model_validate_json(json.dumps(data))


def test_current_reader_does_not_upgrade_a_historical_low_attack_or_infer_scope() -> None:
    historical = resistance_roll(
        PROFILE,
        Contestant("a", 1),
        Contestant("b", 10),
        rule_of_16=False,
        rng=RecordedDice((1, 1, 1, 6, 6, 6)),
    )
    forged = prepare_resistance_attempt(
        PROFILE, Contestant("a", 3), Contestant("b", 10), rule_of_16=False
    )
    with pytest.raises(ValidationError, match="captured preparation"):
        validate_current_resistance_attempt(forged, historical)
    optional = resistance_roll(
        PROFILE,
        Contestant("a", 8),
        Contestant("b", 10),
        rule_of_16=False,
        rng=RecordedDice((4, 4, 4, 6, 6, 5)),
    )
    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    with pytest.raises(ValidationError, match="optional subject"):
        validate_current_resistance_attempt(prepared, optional)
    assert (
        validate_current_resistance_attempt(prepared, optional, allow_optional_subject=True)
        == optional
    )


def test_native_history_rejects_duplicate_or_explicit_null_current_receipt_metadata() -> None:
    from wayfarer.engine.simulation.ability_state import history
    from wayfarer.engine.simulation.ability_types import AbilityEvent, AbilityOutcome
    from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState

    prepared = prepare_resistance_attempt(
        PROFILE, Contestant("a", 8), Contestant("b", 10), rule_of_16=False
    )
    attempt = resistance_attempt(prepared, rng=RecordedDice((4, 4, 4)))
    event = AbilityEvent(
        actor_id="a",
        ability_id="trait:ability",
        target_id="b",
        failed=True,
        outcome=AbilityOutcome(outcome="resisted"),
        checks=(attempt.attacker,),
        resistance_attempt=attempt,
        resistance_preparation=prepared,
    )
    valid = event.model_dump_json()
    assert AbilityEvent.read_native_json(valid) == event
    for encoded in (
        '{"resistance_attempt":null,' + valid[1:],
        '{"resistance_preparation":null,' + valid[1:],
        valid + " ",
    ):
        resources = ResourceState(
            events=(ResourceEvent(id="ability:original", at=0, target_id="a", kind=encoded),)
        )
        with pytest.raises(ValidationError, match="native JSON"):
            history(resources)
    old = AbilityEvent(
        actor_id="a",
        ability_id="trait:ability",
        target_id="b",
        outcome=AbilityOutcome(outcome="resisted"),
    ).model_dump_json()
    for key in ("resistance_attempt", "resistance_preparation"):
        with pytest.raises(ValidationError, match="missing"):
            AbilityEvent.read_native_json("{" + '"' + key + '":null,' + old[1:])
