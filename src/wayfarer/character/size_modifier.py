"""Basic Set Size Modifier cost adjustments for character construction.

The service accepts only catalog-selected context from the Basic Set profile.
It changes point prices for positive ST and HP purchases, caps the discount,
and rounds the final positive point cost upward. It never changes character
statistics, runtime pools, or prototype/Lite behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import require_capabilities
from wayfarer.rules.gurps_characters import (
    SIZE_MODIFIER_CAPABILITY_ID,
    table,
)

BASIC_PROFILE: Final = "gurps-basic-set-4e-2004"
SizeCostTarget = Literal["attribute:st", "secondary:hp"]


class SizeModifierError(ValidationError):
    """Invalid Size Modifier construction with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SizeModifierCost:
    """Trusted provenance for a computed Size Modifier price."""

    target: SizeCostTarget
    base_cost: int
    adjusted_cost: int
    size_modifier: int
    discount_percent: int
    source_id: str
    capability_id: str = SIZE_MODIFIER_CAPABILITY_ID


def discount_percent(profile_id: str, size_modifier: int) -> int:
    """Return the selected source discount, capped at its declared maximum."""

    if profile_id != BASIC_PROFILE:
        raise SizeModifierError(
            "size_modifier.profile", "Size Modifier cost adjustments require the Basic Set profile"
        )
    if type(size_modifier) is not int or size_modifier < 1:
        raise SizeModifierError(
            "size_modifier.range", "Size Modifier cost context must be a positive integer"
        )
    require_capabilities(profile_id, (SIZE_MODIFIER_CAPABILITY_ID,))
    selected = table(profile_id)
    return min(
        selected.size_modifier_discount_cap,
        size_modifier * selected.size_modifier_discount_per_level,
    )


def cost(
    profile_id: str,
    target: SizeCostTarget,
    base_cost: int,
    size_modifier: int,
) -> SizeModifierCost:
    """Apply Size Modifier only to positive ST/HP purchases and round once upward."""

    if target not in ("attribute:st", "secondary:hp"):
        raise SizeModifierError("size_modifier.target", "Unsupported Size Modifier cost target")
    if type(base_cost) is not int:
        raise SizeModifierError("size_modifier.cost", "Point cost must be an integer")
    percent = discount_percent(profile_id, size_modifier)
    if base_cost <= 0:
        adjusted = base_cost
        applied = 0
    else:
        adjusted = int(
            (Decimal(base_cost) * Decimal(100 - percent) / Decimal(100)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        applied = percent
    return SizeModifierCost(
        target=target,
        base_cost=base_cost,
        adjusted_cost=adjusted,
        size_modifier=size_modifier,
        discount_percent=applied,
        source_id=table(profile_id).source_id,
    )
