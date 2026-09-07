"""Catalog metadata for GURPS Fourth Edition attributes and secondary characteristics.

Identifiers, per-level point costs, rounding points and table bounds that a
registered GURPS package carries and that ``wayfarer.character.statistics``
compiles. Entries are numbers and identifiers entered from the frozen baseline
named in docs/gurps-conformance.md; no rulebook prose is reproduced. Everything
is keyed by an exact conformance profile ID.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    SourceReference,
)
from wayfarer.rules.conformance import profile

CAPABILITY_IDS: Final = (
    "gurps.character.primary_attributes",
    "gurps.character.secondary_characteristics",
)


class Attribute(StrEnum):
    ST = "st"
    DX = "dx"
    IQ = "iq"
    HT = "ht"


class Secondary(StrEnum):
    HP = "hp"
    WILL = "will"
    PER = "per"
    FP = "fp"
    BASIC_SPEED = "basic-speed"
    BASIC_MOVE = "basic-move"


class Encumbrance(IntEnum):
    """Load band; the integer value is also the Move step and Dodge penalty."""

    NONE = 0
    LIGHT = 1
    MEDIUM = 2
    HEAVY = 3
    EXTRA_HEAVY = 4


ATTRIBUTE_IDS: Final = MappingProxyType({f"attribute:{a.value}": a for a in Attribute})
SECONDARY_IDS: Final = MappingProxyType({f"secondary:{s.value}": s for s in Secondary})
SECONDARY_NAMES: Final = MappingProxyType(
    {
        Secondary.HP: "HP",
        Secondary.WILL: "Will",
        Secondary.PER: "Per",
        Secondary.FP: "FP",
        Secondary.BASIC_SPEED: "Basic Speed",
        Secondary.BASIC_MOVE: "Basic Move",
    }
)


@dataclass(frozen=True, slots=True)
class StatisticsRules:
    """Profile-selected numbers; identical rows across profiles are still separate pins."""

    profile_id: str
    source_id: str
    source_title: str
    attribute_costs: Mapping[Attribute, int]
    secondary_costs: Mapping[Secondary, int]
    damage_table_maximum_st: int
    attribute_minimum: int = 1
    basic_lift_rounding_threshold: Decimal = Decimal(10)
    encumbrance_multipliers: tuple[int, ...] = (1, 2, 3, 6, 10)
    move_multipliers: tuple[Decimal, ...] = (
        Decimal(1),
        Decimal("0.8"),
        Decimal("0.6"),
        Decimal("0.4"),
        Decimal("0.2"),
    )
    hp_fp_guideline_percent: int = 30
    will_per_guideline_maximum: int = 20


_ATTRIBUTE_COSTS: Final = MappingProxyType(
    {Attribute.ST: 10, Attribute.DX: 20, Attribute.IQ: 20, Attribute.HT: 10}
)
_SECONDARY_COSTS: Final = MappingProxyType(
    {
        Secondary.HP: 2,
        Secondary.WILL: 5,
        Secondary.PER: 5,
        Secondary.FP: 3,
        Secondary.BASIC_SPEED: 5,  # per 0.25
        Secondary.BASIC_MOVE: 5,  # per yard/second
    }
)

RULES: Final = MappingProxyType(
    {
        "gurps-lite-4e-2004": StatisticsRules(
            "gurps-lite-4e-2004",
            "sjg:gurps-lite-4e-2004",
            "GURPS Lite, Fourth Edition",
            _ATTRIBUTE_COSTS,
            _SECONDARY_COSTS,
            damage_table_maximum_st=20,
        ),
        "gurps-basic-set-4e-2004": StatisticsRules(
            "gurps-basic-set-4e-2004",
            "sjg:basic-set-characters-4e-2004",
            "GURPS Basic Set: Characters",
            _ATTRIBUTE_COSTS,
            _SECONDARY_COSTS,
            damage_table_maximum_st=100,
        ),
    }
)


def table(profile_id: str) -> StatisticsRules:
    """Resolve profile metadata by exact ID. Package registration uses this.

    Compilation goes through ``wayfarer.character.statistics.rules`` instead,
    which additionally requires both capabilities to be verified.
    """

    selected = RULES.get(profile_id)
    if selected is None:
        profile(profile_id)
        raise ValidationError(f"Profile has no character statistics rules: {profile_id}")
    return selected


def source(profile_id: str) -> SourceReference:
    selected = table(profile_id)
    return SourceReference(
        id=selected.source_id,
        title=selected.source_title,
        rights="user-supplied-reference",
        citation="Identifiers and costs only; see docs/gurps-conformance.md",
    )


def definitions(profile_id: str) -> tuple[RuleDefinition, ...]:
    """Catalog entries a profile package carries so the compiler can bind them."""

    selected = table(profile_id)
    attributes = tuple(
        RuleDefinition(
            id=key,
            kind=DefinitionKind.ATTRIBUTE,
            name=attribute.value.upper(),
            source_id=selected.source_id,
            point_cost=selected.attribute_costs[attribute],
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.attribute",),
        )
        for key, attribute in ATTRIBUTE_IDS.items()
    )
    secondaries = tuple(
        RuleDefinition(
            id=key,
            kind=DefinitionKind.SECONDARY,
            name=SECONDARY_NAMES[secondary],
            source_id=selected.source_id,
            point_cost=selected.secondary_costs[secondary],
            status=ImplementationStatus.IMPLEMENTED,
            hooks=("character.secondary",),
        )
        for key, secondary in SECONDARY_IDS.items()
    )
    return attributes + secondaries
