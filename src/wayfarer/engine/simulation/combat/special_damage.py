"""Basic Set affliction delivery and special penetration rules.

The caller supplies trusted attack and target facts.  This module decides whether
the delivery can reach the target and separates DR's resistance-roll bonus from
the affliction's own resistance modifier (Campaigns B416; Characters B102-B115).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from wayfarer.engine.rules.types.affliction import (
    AfflictionDelivery,
    AfflictionEffect,
    PenetrationModifier,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class PenetrationContext(Record):
    modifier: PenetrationModifier = "ordinary"
    delivery: AfflictionDelivery = "direct"
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    area_or_cone: bool = False
    open_wound_or_mucous_membrane: bool = False
    bare_skin_or_porous_clothing: bool = False
    tough_skin: bool = False
    sealed: bool = False
    inhaling: bool = True
    holding_breath: bool = False
    supplied_air: bool = False
    respiratory_protection: bool = False
    does_not_breathe: bool = False
    filter_lungs: bool = False
    targeted_sense: str | None = None
    sense_available: bool = True
    protected_sense_bonus: int = Field(default=0, ge=0)
    carrier_hit: bool = False
    carrier_penetration: int = Field(default=0, ge=0)
    side_effect_injury: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_combination(self) -> PenetrationContext:
        if self.modifier == "armor-divisor" and self.armor_divisor == 1:
            raise ValueError("Armor-divisor delivery requires a non-unit divisor")
        if self.modifier != "armor-divisor" and self.armor_divisor != 1:
            raise ValueError("Only Armor Divisor may change the DR divisor")
        if self.modifier == "sense-based" and not self.targeted_sense:
            raise ValueError("Sense-Based delivery requires one authored sense")
        if self.delivery == "side-effect" and self.side_effect_injury < 1:
            raise ValueError("Side Effect requires penetrating injury")
        if self.delivery != "side-effect" and self.side_effect_injury:
            raise ValueError("Only Side Effect accepts injury-derived resistance")
        if self.modifier == "follow-up" and self.delivery != "direct":
            raise ValueError("Follow-Up is itself the delivery path")
        return self


class AfflictionPenetration(Record):
    applies: bool
    dr_bonus: int = Field(default=0, ge=0)
    resistance_modifier: int = Field(default=0, le=0)
    reason: str


def _divided_dr(dr: int, divisor: Decimal) -> int:
    if type(dr) is not int or dr < 0 or not divisor.is_finite() or divisor <= 0:
        raise ValidationError("Affliction DR and divisor must be finite nonnegative facts")
    divided = int(Decimal(dr) / divisor)
    return max(1, divided) if divisor < 1 else divided


def resolve_affliction_penetration(dr: int, context: PenetrationContext) -> AfflictionPenetration:
    """Return the B416 resistance adjustment without rolling or mutating state."""

    if context.delivery == "side-effect":
        return AfflictionPenetration(
            applies=True,
            resistance_modifier=-(context.side_effect_injury // 2),
            reason="side-effect",
        )
    if context.delivery == "linked" and not context.carrier_hit:
        return AfflictionPenetration(applies=False, reason="carrier-missed")

    modifier = context.modifier
    if modifier in ("ordinary", "armor-divisor"):
        return AfflictionPenetration(
            applies=True,
            dr_bonus=_divided_dr(dr, context.armor_divisor),
            reason=modifier,
        )
    if modifier == "follow-up":
        if not context.carrier_hit:
            return AfflictionPenetration(applies=False, reason="carrier-missed")
        return AfflictionPenetration(
            applies=True,
            dr_bonus=0 if context.carrier_penetration else dr,
            reason="follow-up-penetrated" if context.carrier_penetration else "follow-up-stopped",
        )
    if modifier == "blood-agent":
        if context.area_or_cone:
            immune = context.sealed or (
                (context.does_not_breathe or context.filter_lungs)
                and context.respiratory_protection
            )
            return AfflictionPenetration(
                applies=not immune,
                reason="sealed-or-filtered" if immune else "area-blood-agent",
            )
        applies = context.open_wound_or_mucous_membrane
        return AfflictionPenetration(
            applies=applies,
            reason="blood-route" if applies else "no-blood-route",
        )
    if modifier == "contact-agent":
        if context.area_or_cone:
            return AfflictionPenetration(
                applies=not context.sealed,
                reason="sealed" if context.sealed else "area-contact-agent",
            )
        exposed = context.bare_skin_or_porous_clothing
        stopped = dr > 0 and not context.tough_skin
        return AfflictionPenetration(
            applies=exposed and not stopped,
            reason="contact" if exposed and not stopped else "no-contact-route",
        )
    if modifier == "respiratory-agent":
        immune = (
            not context.inhaling
            or context.holding_breath
            or context.supplied_air
            or context.respiratory_protection
            or context.does_not_breathe
            or context.filter_lungs
        )
        return AfflictionPenetration(
            applies=not immune,
            reason="respiratory-route" if not immune else "no-respiratory-route",
        )
    if modifier == "sense-based":
        return AfflictionPenetration(
            applies=context.sense_available,
            dr_bonus=context.protected_sense_bonus if context.sense_available else 0,
            reason="sense-route" if context.sense_available else "sense-unavailable",
        )
    raise ValidationError("Unsupported special penetration modifier")


def active_afflictions(state: ResourceState, actor_id: str) -> tuple[AfflictionEffect, ...]:
    active = set(state.active_effect_ids)
    return tuple(
        effect
        for effect in state.afflictions
        if effect.actor_id == actor_id
        and effect.id in active
        and effect.started_at <= state.game_time < effect.expires_at
    )
