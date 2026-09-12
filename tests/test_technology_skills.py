"""Whole-entry technology, science and vehicle skill procedures (#346).

Independent expectations: Basic Set Characters, Fourth Edition, B168-233 skill
chapter with the B301-304 index (attribute, difficulty, recorded defaults,
specialty and technique per row), B168 for the technology-level difference and
B169 for familiarity. Targets, margins, outcomes and unit counts are pinned in
``tests/fixtures/gurps/technology_skills.json``, worked out from the source rules
rather than generated from the services under test. These constructions use the
selected Characters third-printing baseline; remaining gaps retain concrete owners.
"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from wayfarer.engine.character.compiler import DerivedSheet, PurchasedEntry, ValidatedBuild
from wayfarer.engine.character.technology import operator_from_build
from wayfarer.engine.rules.checks import Modifier, ModifierKind, RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.skills.mundane import PROFILE, audit_report, inventory
from wayfarer.engine.rules.skills.mundane.technology.attempts import (
    Operator,
    Situation,
    attempt,
    replay,
    require_task,
)
from wayfarer.engine.rules.skills.mundane.technology.inventory import PROCEDURES, definitions
from wayfarer.errors import ValidationError

FIXTURE = Path("tests/fixtures/gurps/technology_skills.json")
Case = dict[str, object]

# The exact inventory scope #346 audits, transcribed from the issue.
LISTED = (
    "airshipman architecture astronomy battlesuit bioengineering biology boating cartography "
    "chemistry computer-operation computer-programming crewman criminology cryptography "
    "current-affairs disguise diving-suit driving electrician electronics-operation "
    "electronics-repair engineer environment-suit explosives forensics forward-observer "
    "freight-handling geography geology hazardous-materials intelligence-analysis mathematics "
    "mathematics-applied mathematics-computer-science mathematics-cryptology mathematics-pure "
    "mathematics-statistics mathematics-surveying mechanic metallurgy motion-picture-camera "
    "nbc-suit no-landing-extraction paleontology physics physics-acoustics piloting research "
    "seamanship set-trap shiphandling spacer submarine submariner traps vacc-suit work-by-touch"
).split()
# Rows this issue does not implement, and the concrete open child that owns them.
# #356 expanded the discipline-keyed families; the four whose specialty axis is a
# world, a planet type or a species record that axis but wait on #390 to name one.
TRANSFERRED = {
    "skill:biology": 390,
    "skill:disguise": 390,
    "skill:geography": 390,
    "skill:geology": 390,
    "skill:motion-picture-camera": 338,
}
# Families completed by their concrete specialties instead of a dispatch.
FAMILIES = {
    "skill:bioengineering": 3,
    "skill:boating": 4,
    "skill:crewman": 4,
    "skill:current-affairs": 8,
    "skill:driving": 9,
    "skill:electronics-operation": 9,
    "skill:electronics-repair": 10,
    "skill:engineer": 10,
    "skill:environment-suit": 4,
    "skill:explosives": 5,
    "skill:hazardous-materials": 3,
    "skill:mathematics": 6,
    "skill:mechanic": 29,
    "skill:paleontology": 3,
    "skill:piloting": 14,
    "skill:shiphandling": 4,
    "skill:submarine": 3,
}


def load_cases() -> list[Case]:
    data = json.loads(FIXTURE.read_text())
    assert data["baseline_id"] == BASELINE_ID
    assert data["profile"] == PROFILE
    return cast(list[Case], data["cases"])


def field(case: Case, name: str) -> Case:
    return cast(Case, case[name])


def situation_for(case: Case) -> tuple[Operator, Situation]:
    given = field(case, "input")
    parent = given.get("parent_level")
    operator = Operator(
        cast(str, case["skill_id"]),
        cast(int, given["level"]),
        cast(int, given["operator_tl"]),
        frozenset(cast(list[str], given.get("trained", []))),
        cast(int, parent) if parent is not None else None,
        frozenset(cast(list[str], given.get("purchased_definitions", []))),
        frozenset(cast(list[str], given.get("capabilities", []))),
    )
    return operator, Situation(
        cast(int, given["task_tl"]), cast(bool, given["familiar"]), cast(int, given["handling"])
    )


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: str(case["id"]))
def test_bound_rows_execute_to_hand_entered_expectations(case: Case) -> None:
    operator, situation = situation_for(case)
    dice = cast(list[int], field(case, "input")["dice"])
    result = attempt(operator, situation, rng=RecordedDice(dice))
    expected = field(case, "expected")
    assert result.procedure_id == case["skill_id"]
    assert result.dispatch.value == expected["dispatch"]
    assert result.effect.value == expected["effect"]
    assert result.check.effective_target == expected["effective_target"]
    assert result.check.total == expected["total"]
    assert result.check.margin == expected["margin"]
    assert result.check.outcome.value == expected["outcome"]
    assert result.units == expected["units"]
    assert result.unit == expected["unit"]
    assert result.hazard is expected["hazard"]
    assert result.succeeded == (expected["outcome"] in {"success", "critical-success"})
    assert list(result.activation_blockers) == expected["activation_blockers"]
    # A recorded attempt re-scores to the identical receipt and never rerolls.
    assert replay(result) == result


def test_every_case_binds_a_source_reference_and_exercises_the_whole_catalog() -> None:
    cases = load_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert cast(str, case["reference"]).startswith("B")
        assert case["skill_id"] in PROCEDURES
    dispatches = {field(case, "expected")["dispatch"] for case in cases}
    effects = {field(case, "expected")["effect"] for case in cases}
    outcomes = {field(case, "expected")["outcome"] for case in cases}
    assert dispatches == {
        entry.dispatch for entry in PROCEDURES.values() if entry.dispatch is not None
    }
    assert effects == {
        entry.task.effect.value
        for entry in PROCEDURES.values()
        if entry.dispatchable and entry.task
    }
    assert outcomes == {"critical-success", "success", "failure", "critical-failure"}


def test_listed_scope_is_completely_accounted_for() -> None:
    """#346 acceptance: implemented and tested, or transferred to an open child."""
    rows = {entry.id: entry for entry in inventory()}
    for name in LISTED:
        identifier = f"skill:{name}"
        entry = rows[identifier]
        procedure = PROCEDURES[identifier]
        if identifier in TRANSFERRED:
            assert not procedure.implemented
            owner = TRANSFERRED[identifier]
            assert owner in procedure.owners
            assert owner in entry.followup_issues
            if identifier == "skill:motion-picture-camera":
                # The receiving #338 catalog now supplies the real procedure.
                assert entry.implementation == "implemented"
                assert entry.dispatch == "noncombat.approach"
            else:
                assert entry.implementation == "unsupported"
        else:
            assert entry.implementation == "implemented"
            assert procedure.implemented
        assert entry.available is (entry.dispatch is not None and not entry.blockers)


def test_a_binding_may_only_resolve_or_keep_the_recorded_blockers() -> None:
    """The inventory and the runtime module cannot drift apart in either direction."""
    rows = {entry.id: entry for entry in inventory()}
    for identifier, procedure in PROCEDURES.items():
        recorded = rows[identifier]
        assert set(procedure.resolved).isdisjoint(procedure.blockers)
        assert all(owners for owners in procedure.transferred.values())
        # Blockers the binding keeps survive; the ones it resolves are gone.
        if identifier == "skill:motion-picture-camera":
            assert recorded.dispatch == "noncombat.approach"
            continue
        assert set(procedure.blockers) <= set(recorded.blockers)
        assert set(procedure.resolved).isdisjoint(recorded.blockers)


def test_families_are_expanded_into_concrete_specialties() -> None:
    """B180-B223: an implemented family is completed by real specialties, not a promise."""
    for identifier, count in FAMILIES.items():
        family = PROCEDURES[identifier]
        assert len(family.specialties) == count, identifier
        assert not family.dispatchable
        for child in family.specialties:
            specialty = PROCEDURES[child]
            assert specialty.dispatchable
            assert specialty.specialty is not None
            assert specialty.specialty.family == identifier.removeprefix("skill:")
    assert {entry.specialty.name for entry in PROCEDURES.values() if entry.specialty} >= {
        "automobile",
        "battlesuit",
        "demolition",
        "light-airplane",
        "sailboat",
        "seamanship",
        "starship",
    }


def test_techniques_carry_parent_specific_defaults_and_caps() -> None:
    """B230: a technique is bounded by its own parent, never by a generic rule."""
    expected = {
        "skill:motion-picture-camera": ("skill:photography", -2, 0),
        "skill:no-landing-extraction": ("skill:piloting", -5, 0),
        "skill:set-trap": ("skill:traps", -2, 0),
        "skill:work-by-touch": ("skill:traps", -5, 0),
    }
    for identifier, (parent, default, maximum) in expected.items():
        technique = PROCEDURES[identifier].technique
        assert technique is not None
        assert (technique.parent, technique.default_modifier, technique.maximum_modifier) == (
            parent,
            default,
            maximum,
        )
        # A technique never invents a dispatch its parent does not have. A family
        # parent lends the dispatch its own specialties use, and a parent outside
        # this group lends none at all.
        entry = PROCEDURES[identifier]
        owner = PROCEDURES.get(parent)
        if owner is None:
            assert not entry.dispatchable, identifier
        elif owner.specialties:
            assert {PROCEDURES[child].dispatch for child in owner.specialties} == {
                entry.dispatch
            }, identifier
        else:
            assert entry.dispatch == owner.dispatch, identifier


@pytest.mark.parametrize("level", [8, 15])
def test_a_technique_outside_its_parent_range_is_rejected(level: int) -> None:
    operator = Operator("skill:no-landing-extraction", level, 8, parent_level=14)
    with pytest.raises(ValidationError, match="parent-specific range"):
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))


def test_a_technique_without_its_parent_level_cannot_be_rolled() -> None:
    operator = Operator("skill:work-by-touch", 10, 8)
    with pytest.raises(ValidationError, match="parent skill level"):
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("skill:biology", "runtime-procedure (#390)"),
        ("skill:motion-picture-camera", "runtime-procedure (#338)"),
        ("skill:geology", "runtime-procedure (#390)"),
    ],
)
def test_transferred_rows_fail_closed_naming_their_owner(identifier: str, expected: str) -> None:
    """A transferred row is refused by name; it never falls back to a generic roll."""
    operator = Operator(identifier, 12, 8, parent_level=14)
    with pytest.raises(ValidationError) as error:
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))
    assert "unsupported" in str(error.value)
    assert expected in str(error.value)


def test_a_family_row_requires_a_concrete_specialty() -> None:
    operator = Operator("skill:driving", 12, 8)
    with pytest.raises(ValidationError, match="requires a concrete specialty"):
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))


def test_skills_outside_this_audit_are_refused_rather_than_guessed() -> None:
    operator = Operator("skill:broadsword", 12, 8)
    with pytest.raises(ValidationError, match="outside the technology procedures"):
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))


def test_procedures_fail_closed_outside_the_selected_profile() -> None:
    operator = Operator("skill:research", 13, 8)
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        attempt(
            operator, Situation(8), rng=RecordedDice([3, 3, 3]), profile_id="gurps-lite-4e-2004"
        )


def test_an_untrained_prerequisite_refuses_the_attempt() -> None:
    """B179: Astronomy is not rollable without its trained Mathematics prerequisite."""
    operator = Operator("skill:astronomy", 13, 8)
    with pytest.raises(ValidationError, match="Untrained prerequisite"):
        attempt(operator, Situation(8), rng=RecordedDice([3, 3, 3]))


def test_handling_is_rejected_where_the_procedure_does_not_steer() -> None:
    """B185: a crew station is held, not steered; Handling belongs to the operator."""
    operator = Operator("skill:seamanship", 12, 8)
    with pytest.raises(ValidationError, match="Handling does not apply"):
        attempt(operator, Situation(8, handling=2), rng=RecordedDice([3, 3, 3]))


def test_a_non_positive_effective_skill_is_refused_before_any_die_is_drawn() -> None:
    operator = Operator("skill:research", 0, 8)
    with pytest.raises(ValidationError, match="must be positive"):
        attempt(operator, Situation(8), rng=RecordedDice([]))


def test_situational_modifiers_stay_typed_and_visible_in_the_receipt() -> None:
    """A caller-supplied ruling is recorded as its own modifier, never folded in."""
    operator = Operator("skill:forensics", 14, 8)
    ruling = Modifier(-3, "contaminated-scene", PROFILE, BASELINE_ID, ModifierKind.SITUATIONAL)
    result = attempt(operator, Situation(6, situational=(ruling,)), rng=RecordedDice([3, 3, 3]))
    assert [(m.reason, m.value, m.kind.value) for m in result.check.modifiers] == [
        ("technology-level-difference", -2, "situational"),
        ("contaminated-scene", -3, "situational"),
    ]
    assert result.check.effective_target == 9


def test_recorded_mechanics_and_the_runtime_binding_cannot_drift() -> None:
    """`inventory()` rejects a binding whose numbers differ from the recorded row."""
    rows = {entry.id: entry for entry in inventory()}
    for identifier, procedure in PROCEDURES.items():
        definition = rows[identifier].definition
        assert definition is not None and definition.skill is not None
        assert (
            replace(procedure.spec(), technology_level_required=rows[identifier].tl_required)
            == definition.skill
        )


def test_runtime_operator_uses_the_approved_purchase_tl() -> None:
    """B168: procedure callers derive learned TL and skill level from the build."""
    build = ValidatedBuild(
        "revision",
        GURPS_BASIC_PROFILE.rules,
        (
            PurchasedEntry("skill:physics", 4, 4, 7),
            PurchasedEntry("skill:mathematics-applied", 4, 4, 7),
        ),
        8,
        0,
        DerivedSheet((DerivedValue("skill:physics", Decimal(14), ()),)),
        "Ada",
        "",
    )
    operator = operator_from_build(build, "skill:physics")
    assert (operator.level, operator.technology_level) == (14, 7)
    assert "skill:mathematics-applied" in operator.trained
    result = attempt(operator, Situation(9), rng=RecordedDice([3, 3, 4]))
    assert result.check.effective_target == 12
    assert result.check.modifiers[0].reason == "technology-level-difference"

    missing = replace(
        build,
        purchases=(replace(build.purchases[0], technology_level=None), build.purchases[1]),
    )
    with pytest.raises(ValidationError, match="no TL purchase"):
        operator_from_build(missing, "skill:physics")


def test_only_a_dispatched_row_carries_its_hook() -> None:
    bound = definitions()
    assert len(bound) == sum(entry.dispatchable for entry in PROCEDURES.values())
    for definition in bound:
        assert definition.skill is not None
        entry = PROCEDURES[definition.id]
        assert entry.dispatch is not None
        assert definition.hooks == ("character.gurps-skill", entry.dispatch)
    with pytest.raises(ValidationError, match="no bound dispatch"):
        PROCEDURES["skill:biology"].definition()
    with pytest.raises(ValidationError, match="no bound dispatch"):
        PROCEDURES["skill:driving"].definition()


def test_verified_vehicle_movement_allows_skill_activation() -> None:
    """Completed vehicle mechanics allow their source-entered skills in play."""
    assert require_task(PROFILE, "skill:driving-automobile").activation_blockers == ()
    assert require_task(PROFILE, "skill:research").activation_blockers == ()
    rows = {entry.id: entry for entry in inventory()}
    assert 358 in rows["skill:driving-automobile"].followup_issues


def test_implemented_rows_reach_the_audit_report() -> None:
    report = audit_report()
    counts = cast(dict[str, int], report["implementation_counts"])
    # #342 adds all 22 medicine and mental rows to the prior total.
    assert counts["implemented"] == 396
    rows = {entry.id: entry for entry in inventory()}
    assert rows["skill:vacc-suit"].dispatch == "hazard.exposure"
    assert rows["skill:driving-automobile"].dispatch == "transport.vehicle-control"
    assert rows["skill:driving"].dispatch is None
    assert rows["skill:mechanic"].dispatch is None
    assert rows["skill:mechanic-automobile"].dispatch == "object.repair"


def test_bound_definitions_are_not_yet_carried_by_a_package_pin() -> None:
    """The new pin is a separate, explicit migration; this change does not make one.

    Two things must be settled before `definitions()` can be added to a pin, and
    both are recorded here so neither is discovered by a broken build:

    * `skill:physics` and `skill:physics-acoustics` already exist in the pinned
      package as representative definitions on the `check.target` hook. Binding
      them here changes what those two ids mean, which is a deliberate migration.
    * The recorded Diving Suit default reaches `skill:scuba`, which another group
      owns, so these definitions do not resolve as a standalone catalog.
    """
    from wayfarer.engine.rules.profiles import GURPS_RANGED_SKILLS_PACKAGE as pinned

    bound = {definition.id: definition for definition in definitions()}
    existing = {definition.id: definition for definition in pinned.definitions}
    assert sorted(set(bound) & set(existing)) == ["skill:physics", "skill:physics-acoustics"]
    for identifier in ("skill:physics", "skill:physics-acoustics"):
        assert existing[identifier].hooks == ("character.gurps-skill", "check.target")
        assert bound[identifier].hooks == ("character.gurps-skill", "noncombat.approach")
    referenced = {
        default.target
        for definition in bound.values()
        if definition.skill
        for default in definition.skill.defaults
        if default.target.startswith("skill:")
    }
    # Source defaults may point into another procedure group without activating it.
    external = referenced - set(bound)
    assert {"skill:alchemy", "skill:scuba", "skill:strategy-space"} <= external
    assert len(external) == 33
