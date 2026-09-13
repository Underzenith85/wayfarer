"""Pure Basic Set economics formulas (Campaigns B516-B519)."""

from __future__ import annotations

from wayfarer.engine.rules.checks import Outcome
from wayfarer.errors import ValidationError


def population_modifier(population: int) -> int:
    """Return the authored settlement-size modifier shared by job searches."""
    if type(population) is not int or population < 1:
        raise ValidationError("Job-search population must be positive")
    if population < 100:
        return -3
    if population < 1_000:
        return -2
    if population < 5_000:
        return -1
    if population < 10_000:
        return 0
    if population < 50_000:
        return 1
    if population < 100_000:
        return 2
    return 3


def job_search_adjustment(
    *,
    population: int,
    overqualification: int,
    typical_status: int,
    simultaneous_jobs: int,
    advertising_steps: int,
    lazy: bool,
) -> int:
    """Calculate the source-defined weekly job-search adjustment."""
    if overqualification < 0 or simultaneous_jobs < 1 or advertising_steps < 0:
        raise ValidationError("Invalid authored job-search circumstance")
    qualification = min(2, overqualification)
    return (
        population_modifier(population)
        + qualification
        - 2 * typical_status
        - (simultaneous_jobs - 1)
        + advertising_steps
        - (5 if lazy else 0)
    )


def monthly_income(base_pay: int, outcome: Outcome, margin: int, *, variable: bool) -> int:
    """Resolve a monthly job roll without floating-point money."""
    if type(base_pay) is not int or base_pay < 0:
        raise ValidationError("Monthly pay must be a nonnegative integer")
    if outcome is Outcome.CRITICAL_FAILURE:
        return 0
    if outcome is Outcome.CRITICAL_SUCCESS:
        return base_pay * 3 if variable else base_pay
    if not variable:
        return base_pay
    # Variable work changes by 10% per margin; income cannot become negative.
    return max(0, base_pay * max(0, 10 + margin) // 10)


def loyalty_pay_bonus(offered_pay: int, normal_pay: int) -> int:
    """Return the loyalty bonus from pay above the authored norm."""
    if normal_pay <= 0 or offered_pay < 0:
        raise ValidationError("Hireling pay must be nonnegative with a positive norm")
    return max(0, (offered_pay - normal_pay) * 10 // normal_pay)
