"""Trusted per-command combat semantics; absent recorded features stay legacy."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[frozenset[str]] = ContextVar("combat_protocol_features", default=frozenset())


def preserve_grenade_fuse() -> bool:
    return "grenade-fuse" in _current.get()


def maneuver_budget_enabled() -> bool:
    return "maneuver-budget" in _current.get()


@contextmanager
def combat_generation(features: frozenset[str]) -> Iterator[None]:
    token = _current.set(features)
    try:
        yield
    finally:
        _current.reset(token)


def acrobatic_trait_bonuses_enabled() -> bool:
    return "acrobatic-trait-bonuses" in _current.get()


def ground_dive_step_enabled() -> bool:
    return "ground-dive-step" in _current.get()


def secondary_object_blasts_enabled() -> bool:
    return "secondary-object-blasts" in _current.get()


def missile_interposition_enabled() -> bool:
    return "missile-interposition" in _current.get()


def acrobatic_reaction_attributes_enabled() -> bool:
    return "acrobatic-reaction-attributes" in _current.get()


def paralyze_buckler_drop_enabled() -> bool:
    return "paralyze-buckler-drop" in _current.get()


def rooted_dodge_health_trait_composition() -> bool:
    return "rooted-dodge-health-trait-composition" in _current.get()


def rooted_dodge_haste_composition() -> bool:
    return "rooted-dodge-haste-composition" in _current.get()


def hand_melee_spell_contact_enabled() -> bool:
    return "hand-melee-spell-contact" in _current.get()


def karate_kicking_technique_enabled() -> bool:
    return "karate-kicking-technique" in _current.get()


def acrobatic_condition_checks_enabled() -> bool:
    return "acrobatic-condition-checks" in _current.get()


def staff_form_contact_enabled() -> bool:
    return "staff-form-contact" in _current.get()


def regular_contest_extreme_scores_enabled() -> bool:
    return "regular-contest-extreme-scores" in _current.get()


def interposition_current_step_enabled() -> bool:
    return "interposition-current-step" in _current.get()


def staff_heavy_bare_parry_enabled() -> bool:
    return "staff-heavy-bare-parry" in _current.get()


def retreat_postures_enabled() -> bool:
    return "retreat-postures" in _current.get()


def hand_melee_striking_skills_enabled() -> bool:
    return "hand-melee-striking-skills" in _current.get()


def melee_attack_postures_enabled() -> bool:
    return "melee-attack-postures" in _current.get()


def voluntary_attack_minimum_enabled() -> bool:
    return "voluntary-attack-minimum" in _current.get()


def acrobatic_skill_minimum_enabled() -> bool:
    return "acrobatic-skill-minimum" in _current.get()


def repair_operating_tools_enabled() -> bool:
    return "repair-operating-tools" in _current.get()
