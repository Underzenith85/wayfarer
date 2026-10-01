"""B238/B481 ceremonial outcomes without discarding the real skill or margin."""

from dataclasses import replace

from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import replay_success


def replay_ceremonial_check(check: CheckTrace) -> CheckTrace:
    ordinary = replay_success(check)
    return replace(
        ordinary,
        rule_id="gurps.magic.ceremonial",
        outcome=Outcome.CRITICAL_FAILURE
        if ordinary.total >= 17
        else Outcome.FAILURE
        if ordinary.total == 16
        else ordinary.outcome,
    )
