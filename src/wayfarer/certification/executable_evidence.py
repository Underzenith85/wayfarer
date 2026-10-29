"""Join explicit mechanic obligations to source cases and current executed assertions.

A registry entry is a claim, never evidence by itself. Passing JUnit cases must
record the case and checkout fingerprints after checking the behavioral result.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import Field

from wayfarer.certification.source_ledgers import BASELINE_ID, SOURCE_DIGESTS, AuditRecord
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY

MANIFEST = Path("src/wayfarer/certification/basic_set_audit/executable-evidence.json")
FIXTURE = Path("tests/fixtures/gurps/conformance.json")
PROFILE = "gurps-basic-set-4e-2004"


# Reviewed B348 declaration, independent of surviving fixture/binding rows.
# Optional kernel examples cannot substitute for these actual provider cases.
QUICK_CONTEST_OBLIGATION = "section:campaigns:b348:quick-contests"


@dataclass(frozen=True)
class QuickContestRequirement:
    family: str
    provider: str
    operation: str
    service: str
    capability_id: str
    shape: str

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "family": self.family,
            "provider": self.provider,
            "operation": self.operation,
            "service": self.service,
            "capability_id": self.capability_id,
            "profile": PROFILE,
            "source_id": "sjg:basic-set-campaigns-4e-2004",
        }


QUICK_CONTEST_REQUIREMENTS: Mapping[str, QuickContestRequirement] = MappingProxyType(
    {
        "basic-b348-q1-break-free-first-success": QuickContestRequirement(
            "Q1",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q1-break-free-second-success": QuickContestRequirement(
            "Q1",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q2-break-free-fright-aftermath": QuickContestRequirement(
            "Q2",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q3-break-free-both-fail": QuickContestRequirement(
            "Q3",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q4-break-free-success-tie": QuickContestRequirement(
            "Q4",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q4-break-free-failure-tie": QuickContestRequirement(
            "Q4",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q5-break-free-mixed-victory": QuickContestRequirement(
            "Q5",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-q6-break-free-critical-margin": QuickContestRequirement(
            "Q6",
            "public CombatService / break_free",
            "break_free",
            "native_quick_contest",
            "gurps.check.quick_contest",
            "ordinary",
        ),
        "basic-b348-r7-ability-burning-malediction-literal-draws": QuickContestRequirement(
            "R7",
            "public AbilityService / burning-malediction",
            "burning-malediction",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-failed",
        ),
        "basic-b348-r8-ability-burning-malediction-literal-draws": QuickContestRequirement(
            "R8",
            "public AbilityService / burning-malediction",
            "burning-malediction",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-two",
        ),
        "basic-b348-r9-ability-burning-malediction-literal-draws": QuickContestRequirement(
            "R9",
            "public AbilityService / burning-malediction",
            "burning-malediction",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-two",
        ),
        "basic-b348-r7-ability-mind-reading-literal-draws": QuickContestRequirement(
            "R7",
            "public AbilityService / mind-reading",
            "mind-reading",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-failed",
        ),
        "basic-b348-r8-ability-mind-reading-literal-draws": QuickContestRequirement(
            "R8",
            "public AbilityService / mind-reading",
            "mind-reading",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-two",
        ),
        "basic-b348-r9-ability-mind-reading-literal-draws": QuickContestRequirement(
            "R9",
            "public AbilityService / mind-reading",
            "mind-reading",
            "native_ability_resistance",
            "gurps.check.resistance",
            "ability-two",
        ),
        "basic-b348-r7-creature-gaze": QuickContestRequirement(
            "R7",
            "canonical current Creature system entry",
            "gaze",
            "current_creature_resistance",
            "gurps.check.resistance",
            "creature",
        ),
        "basic-b348-r8-creature-gaze": QuickContestRequirement(
            "R8",
            "canonical current Creature system entry",
            "gaze",
            "current_creature_resistance",
            "gurps.check.resistance",
            "creature",
        ),
        "basic-b348-r9-creature-gaze": QuickContestRequirement(
            "R9",
            "canonical current Creature system entry",
            "gaze",
            "current_creature_resistance",
            "gurps.check.resistance",
            "creature",
        ),
        "basic-b348-r7-mental-mind-reading": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "read",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-mind-probe": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "probe",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-mind-control": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "influence",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-possession": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "possession",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-neutralize": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "neutralize",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-dominance": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "influence",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-terror": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "terror",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-failed",
        ),
        "basic-b348-r7-mental-reading-optional-subject": QuickContestRequirement(
            "R7",
            "trusted supplied-total current Mental system entry",
            "read",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-optional",
        ),
        "basic-b348-r8-mental-reading": QuickContestRequirement(
            "R8",
            "trusted supplied-total current Mental system entry",
            "read",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-two",
        ),
        "basic-b348-r9-mental-reading": QuickContestRequirement(
            "R9",
            "trusted supplied-total current Mental system entry",
            "read",
            "current_mental_resistance",
            "gurps.check.resistance",
            "mental-two",
        ),
    }
)

# These are closed runtime observations, not planning prose or generated oracles.
QUICK_CONTEST_SHAPES: Mapping[str, tuple[frozenset[str], frozenset[str]]] = MappingProxyType(
    {
        "ordinary": (
            frozenset(
                (
                    "approved_raw_ST",
                    "dice_per_attempt",
                    "holder_bonus",
                    "holder_hands",
                    "native_trace_bases",
                    "native_trace_modifier_values",
                    "roll_totals",
                )
            ),
            frozenset(
                (
                    "effective_targets",
                    "grip_retained",
                    "pools_unchanged",
                    "signed_margins",
                    "victory_margin",
                    "winner",
                )
            ),
        ),
        "ability-failed": (
            frozenset(
                (
                    "attacker_dice",
                    "compiled_actor_score",
                    "declared_resister_base",
                    "distance_yards",
                    "native_trace_attacker_base",
                    "native_trace_attacker_modifiers",
                    "resister_dice",
                    "resister_modifiers",
                    "rule_of_16",
                )
            ),
            frozenset(
                (
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
                )
            ),
        ),
        "ability-two": (
            frozenset(
                (
                    "attacker_dice",
                    "compiled_actor_score",
                    "declared_resister_base",
                    "distance_yards",
                    "native_trace_attacker_base",
                    "native_trace_attacker_modifiers",
                    "resister_dice",
                    "resister_modifiers",
                    "rule_of_16",
                )
            ),
            frozenset(
                (
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
                )
            ),
        ),
        "creature": (
            frozenset(
                (
                    "attacker_dice",
                    "compiled_will",
                    "declared_resister_base",
                    "distance_yards",
                    "native_trace_attacker_base",
                    "resister_dice",
                    "rule_of_16",
                    "target_DR",
                    "typed_distance_modifier",
                )
            ),
            frozenset(
                (
                    "affected",
                    "attacker_margin",
                    "damage_dice",
                    "effective_attacker",
                    "resister_margin",
                    "subject_attempted",
                    "target_hp",
                    "victory_margin",
                )
            ),
        ),
        "mental-failed": (
            frozenset(
                (
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
                )
            ),
            frozenset(
                (
                    "affected",
                    "attacker_margin",
                    "control_grants",
                    "new_contest_rng_draws",
                    "revealed_fact_ids",
                    "rule_of_16",
                    "subject_attempted",
                    "suppressions",
                    "world_unchanged",
                )
            ),
        ),
        "mental-optional": (
            frozenset(
                (
                    "actor_adapter_dice",
                    "actor_base",
                    "actor_total",
                    "fatigue_cost",
                    "resister_adapter_dice",
                    "resister_base",
                    "resister_total",
                    "supernatural_attack",
                    "target_living_or_sapient",
                )
            ),
            frozenset(
                (
                    "affected",
                    "fp_after",
                    "ordinary_nested_winner",
                    "ordinary_victory_margin",
                    "rule_of_16",
                    "signed_margins",
                    "subject_attempted",
                    "world_unchanged",
                )
            ),
        ),
        "mental-two": (
            frozenset(
                (
                    "actor_adapter_dice",
                    "actor_base",
                    "actor_total",
                    "fatigue_cost",
                    "resister_adapter_dice",
                    "resister_base",
                    "resister_total",
                    "supernatural_attack",
                    "target_living_or_sapient",
                )
            ),
            frozenset(
                (
                    "affected",
                    "attacker_margin",
                    "fp_after",
                    "learned_surface_fact",
                    "resister_margin",
                    "rule_of_16",
                    "victory_margin",
                )
            ),
        ),
    }
)


class CaseBinding(AuditRecord):
    case_id: str
    tests: tuple[str, ...] = Field(min_length=1)


class ObligationBinding(AuditRecord):
    identifier: str
    case_ids: tuple[str, ...] = Field(min_length=1)


class ExecutionManifest(AuditRecord):
    schema_version: Literal[1]
    baseline_id: str
    source_sha256: dict[str, str]
    cases: tuple[CaseBinding, ...]
    obligations: tuple[ObligationBinding, ...]


def checkout_digest(root: Path) -> str:
    """Fingerprint runtime, tests, fixtures and runners, excluding generated files."""
    digest = hashlib.sha256()
    paths = sorted(
        path
        for directory in ("src", "tests", "scripts")
        for path in (root / directory).rglob("*")
        if path.is_file() and path.suffix in {".py", ".json"}
    )
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def case_digest(case: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest()


def provenance(root: Path, case: dict[str, object]) -> dict[str, str]:
    selected = max(
        (p for p in DEFAULT_REGISTRY.profiles if p.conformance_profile_id == PROFILE),
        key=lambda p: p.version,
    )
    return {
        "conformance_case_id": str(case["id"]),
        "conformance_case_digest": case_digest(case),
        "baseline_id": BASELINE_ID,
        "profile_digest": selected.digest,
        "checkout_digest": checkout_digest(root),
    }


@dataclass(frozen=True)
class ExecutionEvidence:
    verified: frozenset[str]
    problems: dict[str, str]
    error: str | None = None

    def problem(self, identifier: str) -> str | None:
        if self.error is not None:
            return self.error
        if identifier in self.verified:
            return None
        return self.problems.get(identifier, "No explicit executable conformance case binding")


def _quick_contest_nodes(binding: CaseBinding, requirement: QuickContestRequirement) -> bool:
    tags = [re.findall(r"(?:\[|-)(sqlite|postgres)(?=\]|-)", node) for node in binding.tests]
    if requirement.provider.startswith("public "):
        return (
            len(binding.tests) == 2
            and len(set(binding.tests)) == 2
            and all(len(tag) == 1 for tag in tags)
            and {tag[0] for tag in tags} == {"sqlite", "postgres"}
        )
    return len(binding.tests) == 1 and not any(tags)


def _quick_contest_shape(case: dict[str, object], shape: str) -> bool:
    inputs, expected = case.get("input"), case.get("expected")
    input_keys, expected_keys = QUICK_CONTEST_SHAPES[shape]
    return (
        isinstance(inputs, dict)
        and isinstance(expected, dict)
        and set(inputs) == input_keys
        and set(expected) == expected_keys
    )


def _quick_contest_declaration(
    cases: Mapping[str, dict[str, object]],
    bindings: Mapping[str, CaseBinding],
    obligation: ObligationBinding | None,
) -> str | None:
    if obligation is None:
        return None
    if len(obligation.case_ids) != len(set(obligation.case_ids)):
        return "Quick Contests declaration repeats a required case"
    if set(obligation.case_ids) != set(QUICK_CONTEST_REQUIREMENTS):
        return "Quick Contests declaration must contain the exact approved 27-case grid"
    for identifier, requirement in QUICK_CONTEST_REQUIREMENTS.items():
        case, binding = cases.get(identifier), bindings.get(identifier)
        if case is None or binding is None:
            return f"Quick Contests declaration lacks fixture or binding: {identifier}"
        if any(case.get(key) != value for key, value in requirement.metadata.items()):
            return f"Quick Contests source/provider role changed: {identifier}"
        if not _quick_contest_shape(case, requirement.shape):
            return f"Quick Contests closed observation shape changed: {identifier}"
        if not _quick_contest_nodes(binding, requirement):
            return f"Quick Contests provider/backend nodes changed: {identifier}"
    return None


def evaluate_execution(root: Path, report: Path | None) -> ExecutionEvidence:
    """Reject missing, stale, skipped, duplicate or status-only execution evidence."""
    try:
        manifest = ExecutionManifest.model_validate_json((root / MANIFEST).read_text())
        fixture: dict[str, object] = json.loads((root / FIXTURE).read_text())
        raw_cases = fixture["cases"]
        if not isinstance(raw_cases, list):
            raise ValueError("Malformed conformance cases")
        cases: dict[str, dict[str, object]] = {}
        for case in raw_cases:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str):
                raise ValueError("Malformed conformance case")
            if case["id"] in cases:
                raise ValueError("Duplicate conformance case")
            cases[case["id"]] = case
        if (
            manifest.baseline_id != BASELINE_ID
            or fixture.get("baseline_id") != BASELINE_ID
            or manifest.source_sha256 != SOURCE_DIGESTS
        ):
            raise ValueError("Stale executable evidence source pins")
        bindings = {entry.case_id: entry for entry in manifest.cases}
        obligations = {entry.identifier: entry for entry in manifest.obligations}
        if len(bindings) != len(manifest.cases) or len(obligations) != len(manifest.obligations):
            raise ValueError("Duplicate executable evidence binding")
        declaration_problem = _quick_contest_declaration(
            cases, bindings, obligations.get(QUICK_CONTEST_OBLIGATION)
        )
        if report is None:
            raise ValueError("No executed conformance report supplied")
        observed: dict[str, list[ET.Element]] = {}
        for test in ET.parse(report).getroot().iter("testcase"):
            node = test.get("classname", "").replace(".", "/") + ".py::" + test.get("name", "")
            observed.setdefault(node, []).append(test)
        fingerprint = checkout_digest(root)
        selected = max(
            (p for p in DEFAULT_REGISTRY.profiles if p.conformance_profile_id == PROFILE),
            key=lambda p: p.version,
        )
        failures: dict[str, str] = {}
        for identifier, binding in bindings.items():
            case = cases.get(identifier)
            if case is None or (
                case.get("profile") != PROFILE
                or case.get("source_id")
                not in {"sjg:basic-set-characters-4e-2004", "sjg:basic-set-campaigns-4e-2004"}
                or not all(
                    case.get(key) for key in ("reference", "provenance", "input", "expected")
                )
            ):
                failures[identifier] = "Missing or incomplete source-derived case"
                continue
            for node in binding.tests:
                matches = observed.get(node, [])
                if len(matches) != 1:
                    failures[identifier] = "Missing or duplicate executed assertion"
                    break
                test = matches[0]
                if any(test.find(tag) is not None for tag in ("failure", "error", "skipped")):
                    failures[identifier] = "Conformance assertion did not pass"
                    break
                properties = test.findall("properties/property")
                expected = {
                    "conformance_case_id": identifier,
                    "conformance_case_digest": case_digest(case),
                    "baseline_id": BASELINE_ID,
                    "profile_digest": selected.digest,
                    "checkout_digest": fingerprint,
                }
                if any(
                    [p.get("value") for p in properties if p.get("name") == name] != [value]
                    for name, value in expected.items()
                ):
                    failures[identifier] = "Missing or stale behavioral execution provenance"
                    break
        problems: dict[str, str] = (
            {QUICK_CONTEST_OBLIGATION: declaration_problem}
            if declaration_problem is not None
            else {}
        )
        for identifier, obligation in obligations.items():
            if identifier in problems:
                continue
            for case_id in obligation.case_ids:
                if case_id not in bindings:
                    problems[identifier] = "Unregistered conformance case ID"
                    break
                if case_id in failures:
                    problems[identifier] = f"{case_id}: {failures[case_id]}"
                    break
                if identifier.startswith("capability:") and cases[case_id].get(
                    "capability_id"
                ) != identifier.removeprefix("capability:"):
                    problems[identifier] = "Conformance case belongs to another capability"
                    break
        return ExecutionEvidence(frozenset(obligations.keys() - problems.keys()), problems)
    except (OSError, ValueError, KeyError, ET.ParseError) as exc:
        return ExecutionEvidence(frozenset(), {}, f"Unreadable execution evidence: {exc}")
