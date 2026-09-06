"""Server-owned checks, contests, randomness and explanation traces."""

import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from wayfarer.errors import ValidationError
from wayfarer.models import Roll


class RandomSource(Protocol):
    def randbelow(self, exclusive_upper_bound: int, /) -> int: ...


class Outcome(StrEnum):
    CRITICAL_SUCCESS = "critical-success"
    SUCCESS = "success"
    FAILURE = "failure"
    CRITICAL_FAILURE = "critical-failure"


@dataclass(frozen=True, slots=True)
class Modifier:
    value: int
    reason: str
    source_id: str
    source_version: str


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
    target = base_target + sum(modifier.value for modifier in modifiers)
    dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
    assert len(dice) == 3
    typed_dice = (dice[0], dice[1], dice[2])
    total = sum(typed_dice)
    return CheckTrace(
        rules_package,
        rules_version,
        rule_id,
        base_target,
        modifiers,
        target,
        typed_dice,
        total,
        target - total,
        _outcome(total, target),
    )


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
        success=trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS),
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
    first = success_check(
        first_target, rng=rng, rules_package=rules_package, rules_version=rules_version
    )
    second = success_check(
        second_target, rng=rng, rules_package=rules_package, rules_version=rules_version
    )
    successful = [(first_id, first), (second_id, second)]
    successful = [
        (actor, trace)
        for actor, trace in successful
        if trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
    ]
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
