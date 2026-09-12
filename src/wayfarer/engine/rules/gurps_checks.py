"""Profile-selected GURPS success, contest and resistance semantics (#99).

Every service here scores dice through :func:`wayfarer.engine.rules.checks.evaluate_success`,
the same authoritative scorer the prototype package uses, so no second engine
exists. Behaviour is selected by an exact conformance profile and each service
fails closed through :func:`wayfarer.engine.rules.conformance.require_capabilities`.
Randomness is drawn only from the caller-supplied server-owned source, traces
record every die, and ``replay_*`` re-scores recorded dice without rolling.

No rulebook prose is reproduced. Section references live in the fixture ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from wayfarer.engine.rules.checks import (
    CheckTrace,
    Modifier,
    ModifierKind,
    RandomSource,
    RecordedDice,
    draw_dice,
    evaluate_success,
)
from wayfarer.engine.rules.conformance import BASELINE_ID, require_capabilities
from wayfarer.errors import ValidationError

SUCCESS_CAPABILITIES: Final = (
    "gurps.check.success",
    "gurps.check.margin",
    "gurps.check.critical",
)
QUICK_CONTEST_CAPABILITY: Final = "gurps.check.quick_contest"
REGULAR_CONTEST_CAPABILITY: Final = "gurps.check.regular_contest"
RESISTANCE_CAPABILITY: Final = "gurps.check.resistance"

# Rule of 16 is a Basic Set resisted-supernatural-attack rule; the frozen Lite
# artifact has no supernatural attacks, so the cap is outside that profile.
RULE_OF_16_PROFILES: Final = frozenset({"gurps-basic-set-4e-2004"})
RULE_OF_16_FLOOR: Final = 16

# Regular Contest levelling thresholds: both effective skills above the high
# threshold are lowered so the higher becomes it; both below the low threshold
# are raised so the lower becomes it.
REGULAR_CONTEST_HIGH: Final = 14
REGULAR_CONTEST_LOW: Final = 6
DEFAULT_ROUND_LIMIT: Final = 1000


@dataclass(frozen=True, slots=True)
class Contestant:
    id: str
    base_target: int
    modifiers: tuple[Modifier, ...] = ()


def _score(
    profile_id: str,
    rule_id: str,
    contestant: Contestant,
    dice: tuple[int, int, int],
    extra: tuple[Modifier, ...] = (),
) -> CheckTrace:
    return evaluate_success(
        contestant.base_target,
        contestant.modifiers + extra,
        dice,
        rules_package=profile_id,
        rules_version=BASELINE_ID,
        rule_id=rule_id,
    )


def _effective(contestant: Contestant) -> int:
    return contestant.base_target + sum(modifier.value for modifier in contestant.modifiers)


# --- Success rolls -----------------------------------------------------------


def success_roll(
    profile_id: str,
    base_target: int,
    modifiers: tuple[Modifier, ...] = (),
    *,
    rng: RandomSource,
) -> CheckTrace:
    """A single 3d6 success roll with typed modifiers, margin and critical boundaries."""
    require_capabilities(profile_id, SUCCESS_CAPABILITIES)
    return _score(
        profile_id,
        SUCCESS_CAPABILITIES[0],
        Contestant("actor", base_target, modifiers),
        draw_dice(rng),
    )


def replay_success(trace: CheckTrace) -> CheckTrace:
    """Re-score the recorded dice; the result must equal the original receipt."""
    require_capabilities(trace.rules_package, SUCCESS_CAPABILITIES)
    return _score(
        trace.rules_package,
        trace.rule_id,
        Contestant("actor", trace.base_target, trace.modifiers),
        trace.dice,
    )


# --- Repeated attempts -------------------------------------------------------


class RepeatedAttemptPolicy(StrEnum):
    """The four repeated-attempt situations the GM must classify before play."""

    SINGLE_CHANCE = "single-chance"
    RETRY_UNTIL_SUCCESS = "retry-until-success"
    UNKNOWN_UNTIL_LATER = "unknown-until-later"
    HAZARDOUS_FAILURE = "hazardous-failure"


@dataclass(frozen=True, slots=True)
class AttemptTrace:
    profile_id: str
    policy: RepeatedAttemptPolicy
    attempt: int
    check: CheckTrace
    revealed: bool
    hazard: bool


def repeated_attempt(
    profile_id: str,
    policy: RepeatedAttemptPolicy,
    previous: tuple[AttemptTrace, ...],
    base_target: int,
    modifiers: tuple[Modifier, ...] = (),
    *,
    rng: RandomSource,
) -> AttemptTrace:
    """Roll the next attempt at a task, or reject it under the declared policy.

    Cumulative penalties are GM rulings: pass them as ``REPEATED_ATTEMPT`` modifiers.
    """
    require_capabilities(profile_id, SUCCESS_CAPABILITIES)
    for earlier in previous:
        if earlier.profile_id != profile_id or earlier.policy is not policy:
            raise ValidationError("Repeated attempts must share one profile and policy")
    if previous and policy is RepeatedAttemptPolicy.SINGLE_CHANCE:
        raise ValidationError("Only one attempt is permitted for this task")
    if previous and policy is RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER:
        raise ValidationError("The outcome is unknown until later; no retry is possible")
    if any(earlier.check.outcome.succeeded for earlier in previous):
        raise ValidationError("The task already succeeded; nothing remains to retry")
    check = _score(
        profile_id,
        SUCCESS_CAPABILITIES[0],
        Contestant("actor", base_target, modifiers),
        draw_dice(rng),
    )
    return AttemptTrace(
        profile_id,
        policy,
        len(previous) + 1,
        check,
        revealed=policy is not RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER,
        hazard=policy is RepeatedAttemptPolicy.HAZARDOUS_FAILURE and not check.outcome.succeeded,
    )


# --- Quick Contests ----------------------------------------------------------

QuickDecision = Literal["success-over-failure", "margin-of-success", "margin-of-failure", "tie"]


@dataclass(frozen=True, slots=True)
class QuickContestTrace:
    profile_id: str
    first_id: str
    second_id: str
    first: CheckTrace
    second: CheckTrace
    winner: str | None
    decision: QuickDecision
    victory_margin: int


def _decide_quick(
    profile_id: str,
    first: Contestant,
    second: Contestant,
    first_trace: CheckTrace,
    second_trace: CheckTrace,
) -> QuickContestTrace:
    a, b = first_trace.outcome.succeeded, second_trace.outcome.succeeded
    if a != b:
        decision: QuickDecision = "success-over-failure"
        winner = first.id if a else second.id
    elif first_trace.margin == second_trace.margin:
        decision, winner = "tie", None
    else:
        decision = "margin-of-success" if a else "margin-of-failure"
        winner = first.id if first_trace.margin > second_trace.margin else second.id
    victory = abs(first_trace.margin - second_trace.margin) if winner is not None else 0
    return QuickContestTrace(
        profile_id, first.id, second.id, first_trace, second_trace, winner, decision, victory
    )


def _validate_pair(first: Contestant, second: Contestant) -> None:
    if first.id == second.id:
        raise ValidationError("Contestants must have distinct identifiers")


def quick_contest(
    profile_id: str,
    first: Contestant,
    second: Contestant,
    *,
    rng: RandomSource,
) -> QuickContestTrace:
    """Both contestants roll once; margins decide, including when both fail."""
    require_capabilities(profile_id, (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY))
    _validate_pair(first, second)
    first_trace = _score(profile_id, QUICK_CONTEST_CAPABILITY, first, draw_dice(rng))
    second_trace = _score(profile_id, QUICK_CONTEST_CAPABILITY, second, draw_dice(rng))
    return _decide_quick(profile_id, first, second, first_trace, second_trace)


def replay_quick_contest(trace: QuickContestTrace) -> QuickContestTrace:
    require_capabilities(trace.profile_id, (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY))
    first = Contestant(trace.first_id, trace.first.base_target, trace.first.modifiers)
    second = Contestant(trace.second_id, trace.second.base_target, trace.second.modifiers)
    return _decide_quick(
        trace.profile_id,
        first,
        second,
        _score(trace.profile_id, trace.first.rule_id, first, trace.first.dice),
        _score(trace.profile_id, trace.second.rule_id, second, trace.second.dice),
    )


# --- Regular Contests --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegularContestTrace:
    profile_id: str
    first_id: str
    second_id: str
    adjustment: int
    rounds: tuple[tuple[CheckTrace, CheckTrace], ...]
    winner: str


def regular_contest_adjustment(first: Contestant, second: Contestant) -> int:
    """Levelling applied to both contestants so very high or very low skills resolve."""
    high, low = sorted((_effective(first), _effective(second)), reverse=True)
    if low > REGULAR_CONTEST_HIGH:
        return REGULAR_CONTEST_HIGH - high
    if high < REGULAR_CONTEST_LOW:
        return REGULAR_CONTEST_LOW - low
    return 0


def _adjustment_modifiers(profile_id: str, adjustment: int) -> tuple[Modifier, ...]:
    if adjustment == 0:
        return ()
    return (
        Modifier(
            adjustment,
            "regular-contest-levelling",
            profile_id,
            BASELINE_ID,
            ModifierKind.CONTEST_ADJUSTMENT,
        ),
    )


def regular_contest_round(
    profile_id: str,
    first: Contestant,
    second: Contestant,
    *,
    rng: RandomSource,
) -> tuple[CheckTrace, CheckTrace]:
    """One durable round for activities whose elapsed time separates attempts."""
    require_capabilities(profile_id, (*SUCCESS_CAPABILITIES, REGULAR_CONTEST_CAPABILITY))
    _validate_pair(first, second)
    extra = _adjustment_modifiers(profile_id, regular_contest_adjustment(first, second))
    return (
        _score(profile_id, REGULAR_CONTEST_CAPABILITY, first, draw_dice(rng), extra),
        _score(profile_id, REGULAR_CONTEST_CAPABILITY, second, draw_dice(rng), extra),
    )


def _run_regular(
    profile_id: str,
    first: Contestant,
    second: Contestant,
    rng: RandomSource,
    round_limit: int,
) -> RegularContestTrace:
    if round_limit < 1:
        raise ValidationError("Regular contest round limit must be positive")
    adjustment = regular_contest_adjustment(first, second)
    rounds: list[tuple[CheckTrace, CheckTrace]] = []
    while len(rounds) < round_limit:
        first_trace, second_trace = regular_contest_round(profile_id, first, second, rng=rng)
        rounds.append((first_trace, second_trace))
        if first_trace.outcome.succeeded != second_trace.outcome.succeeded:
            winner = first.id if first_trace.outcome.succeeded else second.id
            return RegularContestTrace(
                profile_id, first.id, second.id, adjustment, tuple(rounds), winner
            )
    raise ValidationError(f"Regular contest undecided after {round_limit} rounds")


def regular_contest(
    profile_id: str,
    first: Contestant,
    second: Contestant,
    *,
    rng: RandomSource,
    round_limit: int = DEFAULT_ROUND_LIMIT,
) -> RegularContestTrace:
    """Roll repeatedly until exactly one contestant succeeds in a round."""
    require_capabilities(profile_id, (*SUCCESS_CAPABILITIES, REGULAR_CONTEST_CAPABILITY))
    _validate_pair(first, second)
    return _run_regular(profile_id, first, second, rng, round_limit)


def replay_regular_contest(trace: RegularContestTrace) -> RegularContestTrace:
    require_capabilities(trace.profile_id, (*SUCCESS_CAPABILITIES, REGULAR_CONTEST_CAPABILITY))
    if not trace.rounds:
        raise ValidationError("Recorded regular contest has no rounds")
    first_round = trace.rounds[0]
    extra = _adjustment_modifiers(trace.profile_id, trace.adjustment)
    declared = len(first_round[0].modifiers) - len(extra)
    first = Contestant(
        trace.first_id, first_round[0].base_target, first_round[0].modifiers[:declared]
    )
    declared = len(first_round[1].modifiers) - len(extra)
    second = Contestant(
        trace.second_id, first_round[1].base_target, first_round[1].modifiers[:declared]
    )
    recorded = RecordedDice(die for pair in trace.rounds for check in pair for die in check.dice)
    result = _run_regular(trace.profile_id, first, second, recorded, len(trace.rounds))
    if not recorded.exhausted():
        raise ValidationError("Replay finished before consuming every recorded die")
    return result


# --- Resistance rolls --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResistanceTrace:
    profile_id: str
    contest: QuickContestTrace
    rule_of_16: bool
    affected: bool

    @property
    def attacker(self) -> CheckTrace:
        return self.contest.first

    @property
    def resister(self) -> CheckTrace:
        return self.contest.second


def rule_of_16_modifier(
    profile_id: str, attacker: Contestant, resister: Contestant
) -> tuple[Modifier, ...]:
    """Cap a resisted supernatural attack at the higher of 16 and the resistance."""
    cap = max(RULE_OF_16_FLOOR, _effective(resister))
    excess = _effective(attacker) - cap
    if excess <= 0:
        return ()
    return (Modifier(-excess, "rule-of-16", profile_id, BASELINE_ID, ModifierKind.RULE_OF_16),)


def _decide_resistance(
    profile_id: str,
    attacker: Contestant,
    resister: Contestant,
    attacker_dice: tuple[int, int, int],
    resister_dice: tuple[int, int, int],
    rule_of_16: bool,
) -> ResistanceTrace:
    extra = rule_of_16_modifier(profile_id, attacker, resister) if rule_of_16 else ()
    contest_trace = _decide_quick(
        profile_id,
        attacker,
        resister,
        _score(profile_id, RESISTANCE_CAPABILITY, attacker, attacker_dice, extra),
        _score(profile_id, RESISTANCE_CAPABILITY, resister, resister_dice),
    )
    return ResistanceTrace(
        profile_id, contest_trace, rule_of_16, affected=contest_trace.winner == attacker.id
    )


def resistance_roll(
    profile_id: str,
    attacker: Contestant,
    resister: Contestant,
    *,
    rule_of_16: bool,
    rng: RandomSource,
) -> ResistanceTrace:
    """Quick Contest of attack against resistance; the resister wins ties.

    ``rule_of_16`` must be declared by the caller for a supernatural attack against
    a living target's resistance. It is rejected outside the Basic Set profile.
    """
    require_capabilities(
        profile_id, (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY, RESISTANCE_CAPABILITY)
    )
    _validate_pair(attacker, resister)
    if rule_of_16 and profile_id not in RULE_OF_16_PROFILES:
        raise ValidationError(f"Rule of 16 is outside profile {profile_id}")
    return _decide_resistance(
        profile_id, attacker, resister, draw_dice(rng), draw_dice(rng), rule_of_16
    )


def replay_resistance(trace: ResistanceTrace) -> ResistanceTrace:
    require_capabilities(
        trace.profile_id,
        (*SUCCESS_CAPABILITIES, QUICK_CONTEST_CAPABILITY, RESISTANCE_CAPABILITY),
    )
    attack = trace.attacker
    declared = tuple(m for m in attack.modifiers if m.kind is not ModifierKind.RULE_OF_16)
    attacker = Contestant(trace.contest.first_id, attack.base_target, declared)
    resister = Contestant(
        trace.contest.second_id, trace.resister.base_target, trace.resister.modifiers
    )
    return _decide_resistance(
        trace.profile_id, attacker, resister, attack.dice, trace.resister.dice, trace.rule_of_16
    )
