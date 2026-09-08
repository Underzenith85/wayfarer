"""GURPS Fourth Edition attributes and secondary characteristics, per exact profile.

This module owns purchased primary attributes, independently purchased secondary
characteristics (HP, Will, Per, FP, Basic Speed, Basic Move) and the values that
derive from them (Basic Lift, encumbrance, encumbered Move and Dodge, thrust and
swing damage). Behavior is selected by an exact conformance profile from
``wayfarer.rules.conformance``; the prototype ``package:wayfarer-lite`` is not a
profile and keeps its own compiler path untouched.

Numbers here are point costs, multipliers, rounding points and lookup rows
entered from the frozen baseline named in docs/gurps-conformance.md. No rulebook
prose is reproduced. Fractions stay exact until the source-defined rounding
point; nothing here uses binary floating point.

Purchased build values are separate from depleted runtime pools: ``carry_over``
is the only bridge, and it preserves injury and fatigue instead of healing them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import require_capabilities
from wayfarer.rules.gurps_characters import (
    ATTRIBUTE_IDS,
    CAPABILITY_IDS,
    RULES,
    SECONDARY_IDS,
    Attribute,
    Encumbrance,
    Secondary,
    StatisticsRules,
    definitions,
    source,
    table,
)

__all__ = [
    "ATTRIBUTE_IDS",
    "CAPABILITY_IDS",
    "RULES",
    "SECONDARY_IDS",
    "Advisory",
    "Attribute",
    "CharacterStatistics",
    "DiceExpression",
    "Encumbrance",
    "PointCosts",
    "PrimaryAttributes",
    "RuntimePool",
    "Secondary",
    "SecondaryLevels",
    "StatisticsError",
    "StatisticsRules",
    "attribute_cost",
    "basic_lift",
    "carry_over",
    "compile_statistics",
    "damage",
    "definitions",
    "encumbered_dodge",
    "encumbered_move",
    "encumbrance",
    "encumbrance_thresholds",
    "rules",
    "source",
]


class Advisory(StrEnum):
    """Source guidelines that need GM permission; never silent, never a hard failure."""

    HP_BEYOND_GUIDELINE = "hp.beyond-30-percent-of-st"
    FP_BEYOND_GUIDELINE = "fp.beyond-30-percent-of-ht"
    WILL_ABOVE_20 = "will.above-20"
    PER_ABOVE_20 = "per.above-20"
    WILL_BELOW_BASE = "will.more-than-4-below-iq"
    PER_BELOW_BASE = "per.more-than-4-below-iq"
    SPEED_BEYOND_GUIDELINE = "basic-speed.beyond-2-adjustment"
    MOVE_BEYOND_GUIDELINE = "basic-move.beyond-3-adjustment"


class StatisticsError(ValidationError):
    """A build that the selected profile cannot compile, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DiceExpression:
    dice: int
    add: int

    def __str__(self) -> str:
        return f"{self.dice}d{self.add:+d}" if self.add else f"{self.dice}d"


# ST, thrust dice, thrust add, swing dice, swing add. Rows above 40 are the
# listed five-point steps only; unlisted scores fail closed rather than guess.
_DAMAGE_ROWS: Final = (
    (1, 1, -6, 1, -5),
    (2, 1, -6, 1, -5),
    (3, 1, -5, 1, -4),
    (4, 1, -5, 1, -4),
    (5, 1, -4, 1, -3),
    (6, 1, -4, 1, -3),
    (7, 1, -3, 1, -2),
    (8, 1, -3, 1, -2),
    (9, 1, -2, 1, -1),
    (10, 1, -2, 1, 0),
    (11, 1, -1, 1, 1),
    (12, 1, -1, 1, 2),
    (13, 1, 0, 2, -1),
    (14, 1, 0, 2, 0),
    (15, 1, 1, 2, 1),
    (16, 1, 1, 2, 2),
    (17, 1, 2, 3, -1),
    (18, 1, 2, 3, 0),
    (19, 2, -1, 3, 1),
    (20, 2, -1, 3, 2),
    (21, 2, 0, 4, -1),
    (22, 2, 0, 4, 0),
    (23, 2, 1, 4, 1),
    (24, 2, 1, 4, 2),
    (25, 2, 2, 5, -1),
    (26, 2, 2, 5, 0),
    (27, 3, -1, 5, 1),
    (28, 3, -1, 5, 1),
    (29, 3, 0, 5, 2),
    (30, 3, 0, 5, 2),
    (31, 3, 1, 6, -1),
    (32, 3, 1, 6, -1),
    (33, 3, 2, 6, 0),
    (34, 3, 2, 6, 0),
    (35, 4, -1, 6, 1),
    (36, 4, -1, 6, 1),
    (37, 4, 0, 6, 2),
    (38, 4, 0, 6, 2),
    (39, 4, 1, 7, -1),
    (40, 4, 1, 7, -1),
    (45, 5, 0, 7, 1),
    (50, 5, 2, 8, -1),
    (55, 6, 0, 8, 1),
    (60, 7, -1, 9, 0),
    (65, 7, 1, 9, 2),
    (70, 8, 0, 10, 0),
    (75, 8, 2, 10, 2),
    (80, 9, 0, 11, 0),
    (85, 9, 2, 11, 2),
    (90, 10, 0, 12, 0),
    (95, 10, 2, 12, 2),
    (100, 11, 0, 13, 0),
)
_DAMAGE: Final = MappingProxyType(
    {st: (DiceExpression(td, ta), DiceExpression(sd, sa)) for st, td, ta, sd, sa in _DAMAGE_ROWS}
)


def rules(profile_id: str, *, revision: int = 1) -> StatisticsRules:
    """Resolve profile numbers only when the registry verifies both capabilities."""

    selected = table(profile_id, revision=revision)
    require_capabilities(profile_id, CAPABILITY_IDS)
    return selected


@dataclass(frozen=True, slots=True)
class PrimaryAttributes:
    st: int
    dx: int
    iq: int
    ht: int

    def level(self, attribute: Attribute) -> int:
        return {
            Attribute.ST: self.st,
            Attribute.DX: self.dx,
            Attribute.IQ: self.iq,
            Attribute.HT: self.ht,
        }[attribute]


@dataclass(frozen=True, slots=True)
class SecondaryLevels:
    """Purchased absolute levels; ``None`` keeps the source default at no cost.

    ``basic_speed`` is in quarter units (23 means 5.75) because the source sells
    Basic Speed in 0.25 steps; the default equals DX + HT in those units.
    """

    hp: int | None = None
    will: int | None = None
    per: int | None = None
    fp: int | None = None
    basic_speed: int | None = None
    basic_move: int | None = None

    def level(self, secondary: Secondary) -> int | None:
        return {
            Secondary.HP: self.hp,
            Secondary.WILL: self.will,
            Secondary.PER: self.per,
            Secondary.FP: self.fp,
            Secondary.BASIC_SPEED: self.basic_speed,
            Secondary.BASIC_MOVE: self.basic_move,
        }[secondary]


@dataclass(frozen=True, slots=True)
class PointCosts:
    """Plain dictionaries so the projection serializes into build revisions."""

    attributes: dict[Attribute, int]
    secondaries: dict[Secondary, int]

    @property
    def total(self) -> int:
        return sum(self.attributes.values()) + sum(self.secondaries.values())


@dataclass(frozen=True, slots=True)
class CharacterStatistics:
    """Typed projection of build values. Runtime pools live elsewhere."""

    profile_id: str
    st: int
    dx: int
    iq: int
    ht: int
    hp: int
    will: int
    per: int
    fp: int
    basic_speed: Decimal
    basic_move: int
    dodge: int
    basic_lift: Decimal
    thrust: DiceExpression
    swing: DiceExpression
    costs: PointCosts
    advisories: tuple[Advisory, ...]

    def target_values(self) -> Mapping[str, Decimal]:
        """Bases for the mechanical targets that effects may later modify."""

        return MappingProxyType(
            {
                "secondary:hp": Decimal(self.hp),
                "secondary:will": Decimal(self.will),
                "secondary:per": Decimal(self.per),
                "secondary:fp": Decimal(self.fp),
                "secondary:basic-speed": self.basic_speed,
                "secondary:basic-move": Decimal(self.basic_move),
                "secondary:dodge": Decimal(self.dodge),
                "secondary:basic-lift": self.basic_lift,
            }
        )

    def encumbrance_thresholds(self) -> tuple[Decimal, ...]:
        return encumbrance_thresholds(self.profile_id, self.basic_lift)


def _integer(value: object, code: str, message: str) -> int:
    if type(value) is not int:
        raise StatisticsError(code, message)
    return value


def _exact(value: Decimal) -> Decimal:
    """Drop only trailing zeros so equal values render identically."""

    for exponent in (Decimal(1), Decimal("0.1"), Decimal("0.01")):
        candidate = value.quantize(exponent)
        if candidate == value:
            return candidate
    return value


def attribute_cost(profile_id: str, attribute: Attribute, level: int) -> int:
    selected = rules(profile_id)
    level = _integer(level, "attribute.type", "Attribute levels must be integers")
    if level < selected.attribute_minimum:
        raise StatisticsError(
            "attribute.minimum", f"{attribute.value.upper()} is below the minimum"
        )
    return (level - 10) * selected.attribute_costs[attribute]


def basic_lift(profile_id: str, st: int) -> Decimal:
    """ST squared over five, in pounds, rounded to the nearest whole number from ten up.

    ST squared over five never lands on an exact half, so the nearest-integer rule
    has no tie case to resolve.
    """

    selected = rules(profile_id)
    st = _integer(st, "attribute.type", "ST must be an integer")
    if st < selected.attribute_minimum:
        raise StatisticsError("attribute.minimum", "ST is below the minimum")
    exact = Decimal(st * st) / Decimal(5)
    if exact >= selected.basic_lift_rounding_threshold:
        return exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return _exact(exact)


def damage(profile_id: str, st: int, *, revision: int = 1) -> tuple[DiceExpression, DiceExpression]:
    """Listed damage rows, plus B15 high-ST progression in Basic revision 2.

    Missing intermediate rows below 100 fail closed; no interpolation is assumed.
    """

    selected = rules(profile_id, revision=revision)
    st = _integer(st, "attribute.type", "ST must be an integer")
    if selected.high_st_progression and st >= 100:
        extra_dice = (st - 100) // 10
        thrust, swing = _DAMAGE[100]
        return (
            DiceExpression(thrust.dice + extra_dice, thrust.add),
            DiceExpression(swing.dice + extra_dice, swing.add),
        )
    row = _DAMAGE.get(st)
    if row is None or st > selected.damage_table_maximum_st:
        raise StatisticsError(
            "damage.unsupported_st",
            f"ST {st} has no damage row in profile {profile_id}",
        )
    return row


def encumbrance_thresholds(profile_id: str, lift: Decimal) -> tuple[Decimal, ...]:
    return tuple(lift * multiplier for multiplier in rules(profile_id).encumbrance_multipliers)


def encumbrance(
    profile_id: str, lift: Decimal, carried_pounds: Decimal | int
) -> Encumbrance | None:
    """Load band for a carried weight; ``None`` means the load exceeds Extra-Heavy."""

    if carried_pounds < 0:
        raise StatisticsError("encumbrance.weight", "Carried weight cannot be negative")
    for level, threshold in zip(Encumbrance, encumbrance_thresholds(profile_id, lift), strict=True):
        if carried_pounds <= threshold:
            return level
    return None


def encumbered_move(profile_id: str, basic_move: int, level: Encumbrance) -> int:
    """Basic Move times the band multiplier, fractions dropped, never below one yard."""

    basic_move = _integer(basic_move, "secondary.type", "Basic Move must be an integer")
    if basic_move < 0:
        raise StatisticsError("secondary.minimum", "Basic Move cannot be negative")
    multiplier = rules(profile_id).move_multipliers[level]
    reduced = int((Decimal(basic_move) * multiplier).quantize(Decimal(1), rounding=ROUND_FLOOR))
    return max(reduced, 1) if basic_move >= 1 else 0


def encumbered_dodge(dodge: int, level: Encumbrance) -> int:
    return _integer(dodge, "secondary.type", "Dodge must be an integer") - int(level)


def compile_statistics(
    profile_id: str,
    attributes: PrimaryAttributes,
    purchased: SecondaryLevels | None = None,
    *,
    revision: int = 1,
) -> CharacterStatistics:
    """Derive the projection from purchased levels under one exact profile."""

    selected = rules(profile_id, revision=revision)
    if purchased is None:
        purchased = SecondaryLevels()
    levels = {
        attribute: _integer(
            attributes.level(attribute), "attribute.type", "Attribute levels must be integers"
        )
        for attribute in Attribute
    }
    attribute_costs = {
        attribute: attribute_cost(profile_id, attribute, level)
        for attribute, level in levels.items()
    }
    st, dx, iq, ht = (levels[a] for a in Attribute)
    defaults = {
        Secondary.HP: st,
        Secondary.WILL: iq,
        Secondary.PER: iq,
        Secondary.FP: ht,
        Secondary.BASIC_SPEED: dx + ht,
    }
    resolved: dict[Secondary, int] = {}
    secondary_costs: dict[Secondary, int] = {}
    for secondary, default in defaults.items():
        chosen = purchased.level(secondary)
        if chosen is None:
            chosen = default
        chosen = _integer(chosen, "secondary.type", "Secondary levels must be integers")
        if chosen < 1:
            raise StatisticsError(
                "secondary.minimum", f"{secondary.value} must be at least one unit"
            )
        resolved[secondary] = chosen
        secondary_costs[secondary] = (chosen - default) * selected.secondary_costs[secondary]
    speed = _exact(Decimal(resolved[Secondary.BASIC_SPEED]) / Decimal(4))
    default_move = int(speed.quantize(Decimal(1), rounding=ROUND_FLOOR))
    move = purchased.level(Secondary.BASIC_MOVE)
    if move is None:
        move = default_move
    move = _integer(move, "secondary.type", "Secondary levels must be integers")
    if move < 0:
        raise StatisticsError("secondary.minimum", "basic-move cannot be negative")
    resolved[Secondary.BASIC_MOVE] = move
    secondary_costs[Secondary.BASIC_MOVE] = (move - default_move) * selected.secondary_costs[
        Secondary.BASIC_MOVE
    ]
    advisories: list[Advisory] = []
    percent = selected.hp_fp_guideline_percent
    if abs(resolved[Secondary.HP] - st) * 100 > st * percent:
        advisories.append(Advisory.HP_BEYOND_GUIDELINE)
    if abs(resolved[Secondary.FP] - ht) * 100 > ht * percent:
        advisories.append(Advisory.FP_BEYOND_GUIDELINE)
    if resolved[Secondary.WILL] > selected.will_per_guideline_maximum:
        advisories.append(Advisory.WILL_ABOVE_20)
    if resolved[Secondary.PER] > selected.will_per_guideline_maximum:
        advisories.append(Advisory.PER_ABOVE_20)
    reduction_limit = selected.will_per_reduction_limit
    if reduction_limit is not None:
        if iq - resolved[Secondary.WILL] > reduction_limit:
            advisories.append(Advisory.WILL_BELOW_BASE)
        if iq - resolved[Secondary.PER] > reduction_limit:
            advisories.append(Advisory.PER_BELOW_BASE)
    speed_limit = selected.speed_adjustment_limit_quarters
    if speed_limit is not None and abs(resolved[Secondary.BASIC_SPEED] - dx - ht) > speed_limit:
        advisories.append(Advisory.SPEED_BEYOND_GUIDELINE)
    move_limit = selected.move_adjustment_limit
    if move_limit is not None and abs(move - default_move) > move_limit:
        advisories.append(Advisory.MOVE_BEYOND_GUIDELINE)
    thrust, swing = damage(profile_id, st, revision=revision)
    return CharacterStatistics(
        profile_id=profile_id,
        st=st,
        dx=dx,
        iq=iq,
        ht=ht,
        hp=resolved[Secondary.HP],
        will=resolved[Secondary.WILL],
        per=resolved[Secondary.PER],
        fp=resolved[Secondary.FP],
        basic_speed=speed,
        basic_move=move,
        dodge=default_move + 3,
        basic_lift=basic_lift(profile_id, st),
        thrust=thrust,
        swing=swing,
        costs=PointCosts(attribute_costs, secondary_costs),
        advisories=tuple(advisories),
    )


@dataclass(frozen=True, slots=True)
class RuntimePool:
    """A depleted runtime pool; never part of the compiled build."""

    current: int
    maximum: int


def carry_over(previous: RuntimePool | None, maximum: int) -> RuntimePool:
    """Re-derive a pool for a new maximum while keeping the existing deficit.

    A fresh character starts full. An existing character keeps every point of
    injury or fatigue already taken: recompilation, advancement and migration
    move the ceiling, never the wound. The result never exceeds the new maximum
    and never drops below zero.
    """

    maximum = _integer(maximum, "pool.type", "Pool maximum must be an integer")
    if maximum < 0:
        raise StatisticsError("pool.minimum", "Pool maximum cannot be negative")
    if previous is None:
        return RuntimePool(maximum, maximum)
    if previous.current > previous.maximum or previous.current < 0:
        raise StatisticsError("pool.invalid", "Previous pool is outside its own limits")
    deficit = previous.maximum - previous.current
    return RuntimePool(max(0, maximum - deficit), maximum)
