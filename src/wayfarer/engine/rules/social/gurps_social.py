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
    resolve_quick_contest,
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


def evaluate_reaction(
    profile_id: str,
    modifiers: tuple[ReactionModifier, ...],
    dice: tuple[int, int, int],
) -> ReactionTrace:
    """Score supplied 3d under B494/B560-561: higher totals are better.

    This is also the narrow scorer for a separately staged Diplomacy fallback.
    Its caller must retain the preceding Influence contest and combine its
    result with this reaction; this function never repeats that contest.
    """
    profile(profile_id)
    _validate_modifiers(modifiers)
    if len(dice) != 3 or any(type(die) is not int or not 1 <= die <= 6 for die in dice):
        raise ValidationError("Reaction dice require exactly three integer faces within 1-6")
    total = sum(dice) + sum(m.value for m in modifiers)
    return ReactionTrace(dice, modifiers, total, reaction_outcome(total))


def reaction_roll(
    profile_id: str, modifiers: tuple[ReactionModifier, ...], *, rng: RandomSource
) -> ReactionTrace:
    profile(profile_id)
    _validate_modifiers(modifiers)
    return evaluate_reaction(profile_id, modifiers, draw_dice(rng))


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


def _automatic_influence(
    skill: InfluenceSkill, conditions: InfluenceConditions
) -> Literal["indomitable", "unfazeable", "slave-mentality"] | None:
    automatic: Literal["indomitable", "unfazeable", "slave-mentality"] | None = None
    if conditions.indomitable and not conditions.appropriate_empathy:
        automatic = "indomitable"
    elif skill == "intimidation" and conditions.unfazeable:
        automatic = "unfazeable"
    elif conditions.slave_mentality:
        automatic = "slave-mentality"
    return automatic


def _influence_result(
    skill: InfluenceSkill,
    actor_id: str,
    contest: QuickContestTrace | None,
    automatic: Literal["indomitable", "unfazeable", "slave-mentality"] | None,
    conditions: InfluenceConditions,
) -> InfluenceTrace:
    won = automatic == "slave-mentality" or (contest is not None and contest.winner == actor_id)
    outcome: Reaction = (
        ("very-good" if skill == "sex-appeal" else "good")
        if won
        else "very-bad"
        if conditions.specious_intimidation
        else "bad"
    )
    return InfluenceTrace(contest, outcome, automatic=automatic)


def _with_diplomacy_reaction(prepared: InfluenceTrace, fallback: ReactionTrace) -> InfluenceTrace:
    order = ("disastrous", "very-bad", "bad", "poor", "neutral", "good", "very-good", "excellent")
    outcome = max((prepared.outcome, fallback.outcome), key=order.index)
    return InfluenceTrace(prepared.contest, outcome, fallback, prepared.automatic)


def prepare_influence(
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
    """Resolve only the preceding Influence contest, without a Diplomacy reaction.

    A staged host persists this immutable prerequisite before selecting fallback
    dice. The original immediate caller continues through ``influence_roll``.
    """
    validate_influence(profile_id, skill, conditions)
    _validate_modifiers(modifiers)
    if not actor_id or not npc_id or actor_id == npc_id:
        raise ValidationError("Influence requires distinct actor and subject IDs")
    if type(target) is not int or type(will) is not int:
        raise ValidationError("Influence targets must be integers")
    automatic = _automatic_influence(skill, conditions)
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
    return _influence_result(skill, actor_id, contest, automatic, conditions)


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
    """Keep the legacy contest-then-fallback order and serialized trace intact."""
    prepared = prepare_influence(
        profile_id, skill, actor_id, npc_id, target, will, modifiers, rng=rng, conditions=conditions
    )
    if skill != "diplomacy":
        return prepared
    return _with_diplomacy_reaction(prepared, reaction_roll(profile_id, modifiers, rng=rng))


def validate_prepared_influence(
    profile_id: str,
    skill: InfluenceSkill,
    actor_id: str,
    npc_id: str,
    target: int,
    will: int,
    modifiers: tuple[ReactionModifier, ...],
    prepared: InfluenceTrace,
    *,
    conditions: InfluenceConditions = DEFAULT_INFLUENCE_CONDITIONS,
) -> None:
    """Re-score a frozen prerequisite from its own dice, consuming no entropy."""
    validate_influence(profile_id, skill, conditions)
    _validate_modifiers(modifiers)
    if not actor_id or not npc_id or actor_id == npc_id:
        raise ValidationError("Influence requires distinct actor and subject IDs")
    if type(target) is not int or type(will) is not int:
        raise ValidationError("Influence targets must be integers")
    automatic = _automatic_influence(skill, conditions)
    contest = prepared.contest
    if automatic is None:
        if contest is None:
            raise ValidationError("Prepared Influence is missing its preceding contest")
        for dice in (contest.first.dice, contest.second.dice):
            if len(dice) != 3 or any(type(die) is not int or not 1 <= die <= 6 for die in dice):
                raise ValidationError("Prepared Influence has invalid contest dice")
        contest = resolve_quick_contest(
            profile_id,
            Contestant(actor_id, target + sum(modifier.value for modifier in modifiers)),
            Contestant(npc_id, will),
            first_dice=contest.first.dice,
            second_dice=contest.second.dice,
        )
    else:
        contest = None
    if prepared != _influence_result(skill, actor_id, contest, automatic, conditions):
        raise ValidationError("Prepared Influence does not match its preceding contest")


def resolve_diplomacy(
    profile_id: str,
    actor_id: str,
    npc_id: str,
    target: int,
    will: int,
    modifiers: tuple[ReactionModifier, ...],
    prepared: InfluenceTrace,
    selected: ReactionTrace,
    *,
    conditions: InfluenceConditions = DEFAULT_INFLUENCE_CONDITIONS,
) -> InfluenceTrace:
    """Combine the selected B359 fallback with the unchanged Influence result."""
    validate_prepared_influence(
        profile_id,
        "diplomacy",
        actor_id,
        npc_id,
        target,
        will,
        modifiers,
        prepared,
        conditions=conditions,
    )
    if type(selected.total) is not int or selected != evaluate_reaction(
        profile_id, modifiers, selected.dice
    ):
        raise ValidationError("Selected Diplomacy reaction does not match supplied dice")
    return _with_diplomacy_reaction(prepared, selected)


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
