"""Closed B348 native observations precede selected-source execution provenance."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from wayfarer.certification.executable_evidence import FIXTURE, provenance

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "gurps-basic-set-4e-2004"
SOURCE = "sjg:basic-set-campaigns-4e-2004"


@dataclass(frozen=True)
class Contract:
    family: str
    provider: str
    operation: str
    service: str
    input_keys: frozenset[str]
    expected_keys: frozenset[str]


# Independently frozen from the approved27-case source review, never inferred
# from whichever fixture/binding entries survive an edit.
CONTRACTS: dict[str, Contract] = {
    "basic-b348-q1-break-free-first-success": Contract(
        "Q1",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q1-break-free-second-success": Contract(
        "Q1",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q2-break-free-fright-aftermath": Contract(
        "Q2",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q3-break-free-both-fail": Contract(
        "Q3",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q4-break-free-success-tie": Contract(
        "Q4",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q4-break-free-failure-tie": Contract(
        "Q4",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q5-break-free-mixed-victory": Contract(
        "Q5",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-q6-break-free-critical-margin": Contract(
        "Q6",
        "public CombatService / break_free",
        "break_free",
        "native_quick_contest",
        frozenset(
            [
                "approved_raw_ST",
                "dice_per_attempt",
                "holder_bonus",
                "holder_hands",
                "native_trace_bases",
                "native_trace_modifier_values",
                "roll_totals",
            ]
        ),
        frozenset(
            [
                "effective_targets",
                "grip_retained",
                "pools_unchanged",
                "signed_margins",
                "victory_margin",
                "winner",
            ]
        ),
    ),
    "basic-b348-r7-ability-burning-malediction-literal-draws": Contract(
        "R7",
        "public AbilityService / burning-malediction",
        "burning-malediction",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
            ]
        ),
    ),
    "basic-b348-r8-ability-burning-malediction-literal-draws": Contract(
        "R8",
        "public AbilityService / burning-malediction",
        "burning-malediction",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "resister_margin",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r9-ability-burning-malediction-literal-draws": Contract(
        "R9",
        "public AbilityService / burning-malediction",
        "burning-malediction",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "resister_margin",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r7-ability-mind-reading-literal-draws": Contract(
        "R7",
        "public AbilityService / mind-reading",
        "mind-reading",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
            ]
        ),
    ),
    "basic-b348-r8-ability-mind-reading-literal-draws": Contract(
        "R8",
        "public AbilityService / mind-reading",
        "mind-reading",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "resister_margin",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r9-ability-mind-reading-literal-draws": Contract(
        "R9",
        "public AbilityService / mind-reading",
        "mind-reading",
        "native_ability_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_actor_score",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "native_trace_attacker_modifiers",
                "resister_dice",
                "resister_modifiers",
                "rule_of_16",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "clock_after_activation",
                "clock_before_and_after_resolve",
                "contest_draws",
                "damage_dice",
                "declared_activation_duration_seconds",
                "explicit_wait_seconds",
                "original_activation_fp_after",
                "resister_margin",
                "revealed_fact_ids",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r7-creature-gaze": Contract(
        "R7",
        "canonical current Creature system entry",
        "gaze",
        "current_creature_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_will",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "resister_dice",
                "rule_of_16",
                "target_DR",
                "typed_distance_modifier",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "damage_dice",
                "effective_attacker",
                "resister_margin",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r8-creature-gaze": Contract(
        "R8",
        "canonical current Creature system entry",
        "gaze",
        "current_creature_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_will",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "resister_dice",
                "rule_of_16",
                "target_DR",
                "typed_distance_modifier",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "damage_dice",
                "effective_attacker",
                "resister_margin",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r9-creature-gaze": Contract(
        "R9",
        "canonical current Creature system entry",
        "gaze",
        "current_creature_resistance",
        frozenset(
            [
                "attacker_dice",
                "compiled_will",
                "declared_resister_base",
                "distance_yards",
                "native_trace_attacker_base",
                "resister_dice",
                "rule_of_16",
                "target_DR",
                "typed_distance_modifier",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "damage_dice",
                "effective_attacker",
                "resister_margin",
                "subject_attempted",
                "target_hp",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r7-mental-mind-reading": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "read",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-mind-probe": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "probe",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-mind-control": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "influence",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-possession": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "possession",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-neutralize": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "neutralize",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-dominance": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "influence",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-terror": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "terror",
        "current_mental_resistance",
        frozenset(
            [
                "actor_base",
                "actor_total",
                "adapter_decomposition",
                "definition_id",
                "fatigue_cost",
                "kind",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
                "touching_target",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "control_grants",
                "new_contest_rng_draws",
                "revealed_fact_ids",
                "rule_of_16",
                "subject_attempted",
                "suppressions",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r7-mental-reading-optional-subject": Contract(
        "R7",
        "trusted supplied-total current Mental system entry",
        "read",
        "current_mental_resistance",
        frozenset(
            [
                "actor_adapter_dice",
                "actor_base",
                "actor_total",
                "fatigue_cost",
                "resister_adapter_dice",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
            ]
        ),
        frozenset(
            [
                "affected",
                "fp_after",
                "ordinary_nested_winner",
                "ordinary_victory_margin",
                "rule_of_16",
                "signed_margins",
                "subject_attempted",
                "world_unchanged",
            ]
        ),
    ),
    "basic-b348-r8-mental-reading": Contract(
        "R8",
        "trusted supplied-total current Mental system entry",
        "read",
        "current_mental_resistance",
        frozenset(
            [
                "actor_adapter_dice",
                "actor_base",
                "actor_total",
                "fatigue_cost",
                "resister_adapter_dice",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "fp_after",
                "learned_surface_fact",
                "resister_margin",
                "rule_of_16",
                "victory_margin",
            ]
        ),
    ),
    "basic-b348-r9-mental-reading": Contract(
        "R9",
        "trusted supplied-total current Mental system entry",
        "read",
        "current_mental_resistance",
        frozenset(
            [
                "actor_adapter_dice",
                "actor_base",
                "actor_total",
                "fatigue_cost",
                "resister_adapter_dice",
                "resister_base",
                "resister_total",
                "supernatural_attack",
                "target_living_or_sapient",
            ]
        ),
        frozenset(
            [
                "affected",
                "attacker_margin",
                "fp_after",
                "learned_surface_fact",
                "resister_margin",
                "rule_of_16",
                "victory_margin",
            ]
        ),
    ),
}


def record_quick_contest(
    case_id: str,
    inputs: dict[str, object],
    observed: dict[str, object],
    record_property: Callable[[str, object], None],
) -> None:
    """Compare complete actual observations before emitting each provenance key once.

    Callers assert their actual compiler/statistics profile and retain actual
    package pins separately. The provenance profile is the certification target,
    not a claim that the witness uses the registry's latest runtime package.
    """
    assert case_id in CONTRACTS, "Unreviewed B348 provider/case"
    contract = CONTRACTS[case_id]
    cases: list[dict[str, object]] = json.loads((ROOT / FIXTURE).read_text())["cases"]
    matched = [case for case in cases if case["id"] == case_id]
    assert len(matched) == 1, "B348 fixture must contain one exact reviewed case"
    case = matched[0]
    assert case["profile"] == PROFILE and case["source_id"] == SOURCE
    assert "B348" in str(case["reference"])
    assert case["capability_id"] == (
        "gurps.check.quick_contest" if contract.family.startswith("Q") else "gurps.check.resistance"
    )
    assert (case["family"], case["provider"], case["operation"], case["service"]) == (
        contract.family,
        contract.provider,
        contract.operation,
        contract.service,
    )
    assert set(inputs) == contract.input_keys, "B348 actual input keys differ"
    assert set(observed) == contract.expected_keys, "B348 actual result keys differ"
    assert case["input"] == inputs, "B348 actual input differs from reviewed fixture"
    assert case["expected"] == observed, "B348 actual result differs from reviewed fixture"
    # Exact JSON types matter: True is not the integer1, nor1.0 the integer1.
    for authored, actual in ((case["input"], inputs), (case["expected"], observed)):
        assert json.dumps(authored, sort_keys=True, allow_nan=False) == json.dumps(
            actual, sort_keys=True, allow_nan=False
        ), "B348 exact JSON types differ"
    properties = provenance(ROOT, case)
    for name, value in properties.items():
        record_property(name, value)
