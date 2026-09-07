"""Provisional GURPS social procedures, reconstructed from model knowledge.

Intended sources: Lite 3-4, 10, 24; Characters B120-121; Campaigns
B359-360, B362, B494-495. Exact printing/errata verification is pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import CheckTrace, RandomSource, draw_dice
from wayfarer.rules.conformance import profile
from wayfarer.rules.gurps_checks import Contestant, QuickContestTrace, quick_contest, success_roll
from wayfarer.rules.traits import TraitOptions, TraitRules, cost

Reaction = Literal[
    "disastrous", "very-bad", "bad", "poor", "neutral", "good", "very-good", "excellent"
]
InfluenceSkill = Literal[
    "diplomacy", "fast-talk", "intimidation", "savoir-faire", "sex-appeal", "streetwise"
]


@dataclass(frozen=True)
class ReactionModifier:
    kind: Literal["status", "reputation", "appearance", "situation", "trait"]
    value: int
    source_id: str
    hidden: bool = False


@dataclass(frozen=True)
class ReactionTrace:
    dice: tuple[int, int, int]
    modifiers: tuple[ReactionModifier, ...]
    total: int
    outcome: Reaction


def reaction_outcome(total: int) -> Reaction:
    if type(total) is not int:
        raise ValidationError("Reaction total must be an integer")
    for maximum, outcome in (
        (0, "disastrous"),
        (3, "very-bad"),
        (6, "bad"),
        (9, "poor"),
        (12, "neutral"),
        (15, "good"),
        (18, "very-good"),
    ):
        if total <= maximum:
            return outcome  # type: ignore[return-value]
    return "excellent"


def reaction_roll(
    profile_id: str, modifiers: tuple[ReactionModifier, ...], *, rng: RandomSource
) -> ReactionTrace:
    profile(profile_id)
    if any(type(m.value) is not int or not m.source_id for m in modifiers):
        raise ValidationError("Reaction modifiers require trusted integer values and provenance")
    dice = draw_dice(rng)
    total = sum(dice) + sum(m.value for m in modifiers)
    return ReactionTrace(dice, modifiers, total, reaction_outcome(total))


@dataclass(frozen=True)
class InfluenceTrace:
    contest: QuickContestTrace
    outcome: Reaction
    fallback: ReactionTrace | None = None


def influence_roll(
    profile_id: str,
    skill: InfluenceSkill,
    actor_id: str,
    npc_id: str,
    target: int,
    will: int,
    modifiers: tuple[ReactionModifier, ...],
    *,
    rng: RandomSource,
) -> InfluenceTrace:
    """Use the approved effective skill; bonuses to reactions also affect influence.

    Diplomacy retains an ordinary reaction if the influence result would be worse.
    Sex Appeal yields Very Good on a win; ordinary failed influence yields Bad.
    No outcome compels a player action or reveals an NPC's private motivations.
    """
    if skill not in (
        "diplomacy",
        "fast-talk",
        "intimidation",
        "savoir-faire",
        "sex-appeal",
        "streetwise",
    ):
        raise ValidationError("Unsupported influence skill")
    profile(profile_id)
    contest = quick_contest(
        profile_id,
        Contestant(actor_id, target + sum(m.value for m in modifiers)),
        Contestant(npc_id, will),
        rng=rng,
    )
    outcome: Reaction = (
        ("very-good" if skill == "sex-appeal" else "good") if contest.winner == actor_id else "bad"
    )
    fallback = reaction_roll(profile_id, modifiers, rng=rng) if skill == "diplomacy" else None
    order = ("disastrous", "very-bad", "bad", "poor", "neutral", "good", "very-good", "excellent")
    if fallback and order.index(fallback.outcome) > order.index(outcome):
        outcome = fallback.outcome
    return InfluenceTrace(contest, outcome, fallback)


def self_control_roll(
    profile_id: str,
    base_cost: int,
    levels: int,
    options: TraitOptions,
    rules: TraitRules,
    *,
    rng: RandomSource,
) -> CheckTrace:
    if rules.profile_id != profile_id or not rules.self_control or options.self_control is None:
        raise ValidationError("Requires a compiled self-control disadvantage in this profile")
    cost(base_cost, levels, options, rules)
    return success_roll(profile_id, options.self_control, rng=rng)


@dataclass(frozen=True)
class FrightTrace:
    check: CheckTrace
    table_dice: tuple[int, int, int] | None
    table_total: int | None
    consequence_status: Literal["none", "awaiting-reviewed-table"]


def fright_roll(profile_id: str, will: int, modifier: int = 0, *, rng: RandomSource) -> FrightTrace:
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Fright checks require the Basic Set profile")
    check = success_roll(profile_id, min(13, will + modifier), rng=rng)
    if check.outcome.succeeded:
        return FrightTrace(check, None, None, "none")
    dice = draw_dice(rng)
    return FrightTrace(check, dice, sum(dice) + max(0, -check.margin), "awaiting-reviewed-table")
