"""Exact B378-B379 DR division shared by penetration consumers."""

from decimal import Decimal


def divided_dr(resistance: int, divisor: Decimal) -> int:
    """Floor nonnegative DR by a validated positive finite armor divisor.

    Integer ratios keep rounding, precision, exponent limits and signal flags
    in the caller's Decimal context from changing a printed DR rule. Fractional
    divisors' minimum-DR policies belong to the specific delivery consumer.
    """
    numerator, denominator = divisor.as_integer_ratio()
    return resistance * denominator // numerator
