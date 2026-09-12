"""Server-owned checks, contests, randomness and explanation traces."""

import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, overload

from wayfarer.errors import ValidationError
from wayfarer.models import Roll


class RandomSource(Protocol):
    def randbelow(self, exclusive_upper_bound: int, /) -> int: ...


class NoRandom:
    """Allow deterministic-only calls, but fail closed if they need an omitted RNG."""

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        raise ValidationError("Resolution requires an explicit random source")


NO_RANDOM = NoRandom()


class Outcome(StrEnum):
    CRITICAL_SUCCESS = "critical-success"
    SUCCESS = "success"
    FAILURE = "failure"
    CRITICAL_FAILURE = "critical-failure"

    @property
    def succeeded(self) -> bool:
        return self in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)


class ModifierKind(StrEnum):
    """Why a modifier applies; receipts keep every kind visible and separately auditable."""

    SITUATIONAL = "situational"
    EQUIPMENT = "equipment"
    TRAIT = "trait"
    TIME = "time"
    REPEATED_ATTEMPT = "repeated-attempt"
    CONTEST_ADJUSTMENT = "contest-adjustment"
    RULE_OF_16 = "rule-of-16"


@dataclass(frozen=True, slots=True)
class Modifier:
    value: int
    reason: str
    source_id: str
    source_version: str
    kind: ModifierKind = ModifierKind.SITUATIONAL


@dataclass(frozen=True, slots=True)
class CheckTrace:
    rules_package: str
    rules_version: str
    rule_id: str
    base_target: int
    modifiers: tuple[Modifier, ...]
    effective_target: int
    dice: tuple[int, int, int]
    total: int
    margin: int
    outcome: Outcome


def _outcome(total: int, target: int) -> Outcome:
    if total <= 4 or (total == 5 and target >= 15) or (total == 6 and target >= 16):
        return Outcome.CRITICAL_SUCCESS
    if total == 18 or (total == 17 and target <= 15) or total - target >= 10:
        return Outcome.CRITICAL_FAILURE
    return Outcome.SUCCESS if total <= target and total != 17 else Outcome.FAILURE


@overload
def draw_dice(rng: RandomSource) -> tuple[int, int, int]: ...


@overload
def draw_dice(rng: RandomSource, count: int) -> tuple[int, ...]: ...


def draw_dice(rng: RandomSource, count: int = 3) -> tuple[int, ...]:
    """Consume six-sided dice in order; checks default to three, damage names its count."""
    if count < 0:
        raise ValidationError("Dice count must be nonnegative")
    return tuple(rng.randbelow(6) + 1 for _ in range(count))


def draw_index(rng: RandomSource, count: int) -> int:
    """Select an index without turning a non-d6 choice into a dice roll."""
    if count < 1:
        raise ValidationError("Random selection requires at least one candidate")
    return rng.randbelow(count)


def evaluate_success(
    base_target: int,
    modifiers: tuple[Modifier, ...],
    dice: tuple[int, int, int],
    *,
    rules_package: str,
    rules_version: str,
    rule_id: str,
) -> CheckTrace:
    """Score already-rolled dice. Pure: replaying a recorded trace never rerolls."""
    if any(die < 1 or die > 6 for die in dice):
        raise ValidationError(f"Dice outside 1-6: {dice}")
    target = base_target + sum(modifier.value for modifier in modifiers)
    total = sum(dice)
    return CheckTrace(
        rules_package,
        rules_version,
        rule_id,
        base_target,
        modifiers,
        target,
        dice,
        total,
        target - total,
        _outcome(total, target),
    )


def success_check(
    base_target: int,
    modifiers: tuple[Modifier, ...] = (),
    *,
    rng: RandomSource = secrets,
    rules_package: str,
    rules_version: str,
    rule_id: str = "check:success",
) -> CheckTrace:
    if rule_id != "check:success":
        raise ValidationError(f"Unsupported check: {rule_id}")
    return evaluate_success(
        base_target,
        modifiers,
        draw_dice(rng),
        rules_package=rules_package,
        rules_version=rules_version,
        rule_id=rule_id,
    )


class RecordedDice:
    """Replay source: yields recorded dice in order and refuses to invent new ones."""

    def __init__(self, dice: Iterable[int]) -> None:
        self._dice = iter(dice)

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        try:
            value = next(self._dice)
        except StopIteration:
            raise ValidationError("Replay requested more dice than were recorded") from None
        if not 1 <= value <= exclusive_upper_bound:
            raise ValidationError(f"Recorded die {value} is outside 1-{exclusive_upper_bound}")
        return value - 1

    def exhausted(self) -> bool:
        return next(self._dice, None) is None


def roll(target: int, rng: RandomSource = secrets) -> Roll:
    """Compatibility projection for the existing demo resolution pipeline."""
    trace = success_check(
        target,
        rng=rng,
        rules_package="package:wayfarer-lite",
        rules_version="1.0.0",
    )
    critical: Literal["success", "failure"] | None = (
        "success"
        if trace.outcome is Outcome.CRITICAL_SUCCESS
        else "failure"
        if trace.outcome is Outcome.CRITICAL_FAILURE
        else None
    )
    return Roll(
        dice=list(trace.dice),
        total=trace.total,
        target=trace.effective_target,
        success=trace.outcome.succeeded,
        critical=critical,
    )


@dataclass(frozen=True, slots=True)
class ContestTrace:
    first: CheckTrace
    second: CheckTrace
    winner: str | None


def contest(
    first_id: str,
    first_target: int,
    second_id: str,
    second_target: int,
    *,
    rng: RandomSource,
    rules_package: str,
    rules_version: str,
) -> ContestTrace:
    """Prototype-package contest.

    This keeps the original ``package:wayfarer-lite`` semantics: failed rolls are
    discarded and criticals outrank margins. It is not GURPS conformance evidence;
    profile-selected Quick Contests live in :mod:`wayfarer.engine.rules.gurps_checks`.
    """
    first = success_check(
        first_target, rng=rng, rules_package=rules_package, rules_version=rules_version
    )
    second = success_check(
        second_target, rng=rng, rules_package=rules_package, rules_version=rules_version
    )
    successful = [(first_id, first), (second_id, second)]
    successful = [(actor, trace) for actor, trace in successful if trace.outcome.succeeded]
    if not successful:
        return ContestTrace(first, second, None)
    successful.sort(
        key=lambda item: (item[1].outcome is Outcome.CRITICAL_SUCCESS, item[1].margin, item[0]),
        reverse=True,
    )
    if (
        len(successful) == 2
        and successful[0][1].outcome == successful[1][1].outcome
        and successful[0][1].margin == successful[1][1].margin
    ):
        return ContestTrace(first, second, None)
    return ContestTrace(first, second, successful[0][0])
