"""GURPS social procedures using the shared check and contest scorers.

Basic Set Fourth Edition: Characters B120-121; Campaigns B359-361,
B494-495. Numeric source evidence is recorded in the social tests; full
profile certification and lasting-consequence integration remain separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Modifier, RandomSource, draw_dice
from wayfarer.engine.rules.conformance import profile
from wayfarer.engine.rules.fright import FrightEffect, fright_effect
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    quick_contest,
    success_roll,
)
from wayfarer.engine.rules.traits.base import TraitOptions, TraitRules, cost
from wayfarer.errors import ValidationError

Reaction = Literal[
    "disastrous", "very-bad", "bad", "poor", "neutral", "good", "very-good", "excellent"
]
InfluenceSkill = Literal[
    "diplomacy", "fast-talk", "intimidation", "savoir-faire", "sex-appeal", "streetwise"
]
INFLUENCE_SKILLS: tuple[InfluenceSkill, ...] = (
    "diplomacy",
    "fast-talk",
    "intimidation",
    "savoir-faire",
    "sex-appeal",
    "streetwise",
)


def influence_procedure(skill_id: str) -> InfluenceSkill:
    """Resolve a pinned skill ID, including required Savoir-Faire specialties."""
    for skill in INFLUENCE_SKILLS:
        if skill_id == "skill:" + skill:
            return skill
    if skill_id.startswith("skill:savoir-faire-") and len(skill_id) > len("skill:savoir-faire-"):
        return "savoir-faire"
    raise ValidationError("Unsupported influence skill ID")


@dataclass(frozen=True)
class InfluenceConditions:
    """Trusted B359 circumstances, never player-supplied roll outcomes.

    Empathy must match the subject's kind. Specious intimidation is an authored
    assessment of the threat. Trait-bearing subjects must be bound from their
    approved build by the caller, just like their Will.
    """

    indomitable: bool = False
    appropriate_empathy: bool = False
    unfazeable: bool = False
    slave_mentality: bool = False
    specious_intimidation: bool = False


DEFAULT_INFLUENCE_CONDITIONS = InfluenceConditions()


def validate_influence(
    profile_id: str, skill: InfluenceSkill, conditions: InfluenceConditions
) -> None:
    profile(profile_id)
    if skill not in INFLUENCE_SKILLS:
        raise ValidationError("Unsupported influence skill")
    if any(type(value) is not bool for value in vars(conditions).values()):
        raise ValidationError("Influence conditions require trusted boolean values")
    if conditions.specious_intimidation and skill != "intimidation":
        raise ValidationError("Specious intimidation requires Intimidation")
    if profile_id != "gurps-basic-set-4e-2004" and conditions != InfluenceConditions():
        raise ValidationError("Special influence conditions require the Basic Set profile")
    if conditions.slave_mentality and (
        conditions.indomitable or (conditions.unfazeable and skill == "intimidation")
    ):
        raise ValidationError("Conflicting automatic influence outcomes require adjudication")


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
    _validate_modifiers(modifiers)
    dice = draw_dice(rng)
    total = sum(dice) + sum(m.value for m in modifiers)
    return ReactionTrace(dice, modifiers, total, reaction_outcome(total))


def _validate_modifiers(modifiers: tuple[ReactionModifier, ...]) -> None:
    if any(
        type(m.value) is not int
        or not m.source_id
        or m.kind not in ("status", "reputation", "appearance", "situation", "trait")
        for m in modifiers
    ):
        raise ValidationError("Reaction modifiers require trusted integer values and provenance")
    if len({(m.kind, m.source_id) for m in modifiers}) != len(modifiers):
        raise ValidationError("Duplicate reaction modifier source")


@dataclass(frozen=True)
class InfluenceTrace:
    contest: QuickContestTrace | None
    outcome: Reaction
    fallback: ReactionTrace | None = None
    automatic: Literal["indomitable", "unfazeable", "slave-mentality"] | None = None


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
    conditions: InfluenceConditions = DEFAULT_INFLUENCE_CONDITIONS,
) -> InfluenceTrace:
    """Use the approved effective skill; bonuses to reactions also affect influence.

    Diplomacy retains an ordinary reaction if the influence result would be worse.
    Sex Appeal yields Very Good on a win; ordinary failed influence yields Bad.
    No outcome compels a player action or reveals an NPC's private motivations.
    """
    validate_influence(profile_id, skill, conditions)
    _validate_modifiers(modifiers)
    if not actor_id or not npc_id or actor_id == npc_id:
        raise ValidationError("Influence requires distinct actor and subject IDs")
    if type(target) is not int or type(will) is not int:
        raise ValidationError("Influence targets must be integers")
    automatic: Literal["indomitable", "unfazeable", "slave-mentality"] | None = None
    if conditions.indomitable and not conditions.appropriate_empathy:
        automatic = "indomitable"
    elif skill == "intimidation" and conditions.unfazeable:
        automatic = "unfazeable"
    elif conditions.slave_mentality:
        automatic = "slave-mentality"
    contest = (
        None
        if automatic is not None
        else quick_contest(
            profile_id,
            Contestant(actor_id, target + sum(m.value for m in modifiers)),
            Contestant(npc_id, will),
            rng=rng,
        )
    )
    won = automatic == "slave-mentality" or (contest is not None and contest.winner == actor_id)
    outcome: Reaction = (
        ("very-good" if skill == "sex-appeal" else "good")
        if won
        else "very-bad"
        if conditions.specious_intimidation
        else "bad"
    )
    fallback = reaction_roll(profile_id, modifiers, rng=rng) if skill == "diplomacy" else None
    order = ("disastrous", "very-bad", "bad", "poor", "neutral", "good", "very-good", "excellent")
    if fallback and order.index(fallback.outcome) > order.index(outcome):
        outcome = fallback.outcome
    return InfluenceTrace(contest, outcome, fallback, automatic)


def self_control_roll(
    profile_id: str,
    base_cost: int,
    levels: int,
    options: TraitOptions,
    rules: TraitRules,
    *,
    rng: RandomSource,
    modifiers: tuple[Modifier, ...] = (),
) -> CheckTrace:
    if rules.profile_id != profile_id or not rules.self_control or options.self_control is None:
        raise ValidationError("Requires a compiled self-control disadvantage in this profile")
    cost(base_cost, levels, options, rules)
    if any(type(m.value) is not int or not m.source_id for m in modifiers):
        raise ValidationError("Self-control modifiers require integer values and provenance")
    return success_roll(profile_id, options.self_control, modifiers, rng=rng)


@dataclass(frozen=True)
class FrightTrace:
    check: CheckTrace
    table_dice: tuple[int, int, int] | None
    table_total: int | None
    consequence_status: Literal["none", "resolved"]
    effect: FrightEffect | None = None


def fright_roll(
    profile_id: str,
    will: int,
    modifier: int = 0,
    *,
    rng: RandomSource,
    ht: int = 10,
    check_modifiers: tuple[Modifier, ...] = (),
) -> FrightTrace:
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Fright checks require the Basic Set profile")
    if any(type(value) is not int for value in (will, modifier, ht)) or ht < 1:
        raise ValidationError("Fright checks require integer Will/modifier and positive HT")
    if any(type(m.value) is not int or not m.source_id for m in check_modifiers):
        raise ValidationError("Fright check modifiers require integer values and provenance")
    adjustment = sum(m.value for m in check_modifiers)
    # Apply the Rule of 14 after all modifiers, preserving each check penalty.
    check = success_roll(
        profile_id, min(13 - adjustment, will + modifier), check_modifiers, rng=rng
    )
    if check.outcome.succeeded:
        return FrightTrace(check, None, None, "none")
    dice = draw_dice(rng)
    total = sum(dice) + max(0, -check.margin)
    return FrightTrace(
        check,
        dice,
        total,
        "resolved",
        fright_effect(total, ht, rng=rng, check_modifiers=check_modifiers),
    )
