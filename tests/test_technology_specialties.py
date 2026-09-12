"""Discipline-keyed technology skill specialties (#356).

Independent expectations: Basic Set Characters, Fourth Edition, B180 Bioengineering
and Biology, B186 Current Affairs, B187 Disguise, B189 Electronics Operation, B190
Electronics Repair and Engineer, B198 Geography and Geology, B199 Hazardous
Materials, B207 Mechanic and B212 Paleontology, with the B301-B304 index. Targets,
margins, outcomes and unit counts are pinned in
``tests/fixtures/gurps/technology_specialties.json``, worked out from the source
rules rather than generated from the services under test. These constructions
use the selected third-printing Characters baseline.
"""

import json
from pathlib import Path
from typing import cast

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.rules.mundane_skills import PROFILE, inventory, source_index
from wayfarer.rules.mundane_skills.technology import (
    ELECTRONICS_SPECIALTIES,
    MECHANIC_FAMILIES,
    OPEN_FAMILIES,
    PROCEDURES,
    REPAIRABLE_ELECTRONICS,
    SCIENCE_FAMILIES,
    VEHICLE_FAMILIES,
    Operator,
    Situation,
    attempt,
    require_task,
)

FIXTURE = Path("tests/fixtures/gurps/technology_specialties.json")
Case = dict[str, object]

# The exact inventory scope #356 audits, transcribed from the issue.
LISTED = (
    "bioengineering biology current-affairs disguise electronics-operation electronics-repair "
    "engineer geography geology hazardous-materials mechanic paleontology"
).split()
# Rows whose specialty axis is a world, a planet type, a species or a region.
# The axis is recorded here; naming one subject is #390's.
OPEN = {"skill:biology", "skill:disguise", "skill:geography", "skill:geology"}
OPEN_SUBJECT_OWNER = 390


def load_cases() -> list[Case]:
    data = json.loads(FIXTURE.read_text())
    assert data["baseline_id"] == BASELINE_ID
    assert data["profile"] == PROFILE
    assert data["owner"] == 356
    return cast(list[Case], data["cases"])


def field(case: Case, name: str) -> Case:
    return cast(Case, case[name])


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: str(case["id"]))
def test_expanded_specialties_execute_to_hand_entered_expectations(case: Case) -> None:
    given = field(case, "input")
    operator = Operator(
        cast(str, case["skill_id"]),
        cast(int, given["level"]),
        cast(int, given["operator_tl"]),
    )
    situation = Situation(
        cast(int, given["task_tl"]), cast(bool, given["familiar"]), cast(int, given["handling"])
    )
    result = attempt(operator, situation, rng=RecordedDice(cast(list[int], given["dice"])))
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
    assert list(result.activation_blockers) == expected["activation_blockers"]


def test_every_case_names_a_source_reference_and_a_row_this_issue_expanded() -> None:
    cases = load_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    families = {*SCIENCE_FAMILIES, "mechanic"}
    for case in cases:
        assert cast(str, case["reference"]).startswith("B")
        entry = PROCEDURES[cast(str, case["skill_id"])]
        assert entry.specialty is not None and entry.specialty.family in families
    # Every task shape and every outcome this expansion introduces is exercised.
    assert {field(case, "expected")["unit"] for case in cases} == {
        "containment-held",
        "design-step",
        "finding",
        "news-item",
        "reading",
        "restored-hp",
    }
    assert {field(case, "expected")["outcome"] for case in cases} == {
        "critical-success",
        "success",
        "failure",
        "critical-failure",
    }


def test_listed_scope_is_completely_accounted_for() -> None:
    """#356 acceptance: implemented and tested, or transferred to an open child."""
    rows = {entry.id: entry for entry in inventory()}
    for name in LISTED:
        identifier = f"skill:{name}"
        entry, procedure = rows[identifier], PROCEDURES[identifier]
        if identifier in OPEN:
            assert entry.implementation == "unsupported"
            assert not procedure.implemented
            assert procedure.transferred["runtime-procedure"] == (OPEN_SUBJECT_OWNER,)
            assert OPEN_SUBJECT_OWNER in entry.followup_issues
        else:
            assert entry.implementation == "implemented"
            assert procedure.specialties
        # The specialty axis is recorded for every row either way.
        assert "specialty-expansion" not in entry.blockers
        assert entry.available is (entry.dispatch is not None and not entry.blockers)


def test_each_specialty_rolls_against_its_own_family_numbers() -> None:
    """B169: a specialty is the same skill applied narrowly, not a different one."""
    for family in (*SCIENCE_FAMILIES, "mechanic"):
        parent = PROCEDURES[f"skill:{family}"]
        assert parent.specialties and not parent.dispatchable
        for child in parent.specialties:
            specialty = PROCEDURES[child]
            assert specialty.dispatchable
            assert specialty.specialty is not None
            assert specialty.specialty.family == family
            assert (specialty.attribute, specialty.difficulty) == (
                parent.attribute,
                parent.difficulty,
            )
            assert specialty.defaults == parent.defaults
            assert specialty.page == parent.page


def test_a_family_row_is_refused_by_name_with_the_specialties_it_expands_into() -> None:
    operator = Operator("skill:electronics-repair", 12, 9)
    with pytest.raises(ValidationError, match="requires a concrete specialty") as error:
        attempt(operator, Situation(9), rng=RecordedDice([3, 3, 3]))
    assert "skill:electronics-repair-computers" in str(error.value)


def test_mechanic_is_derived_from_the_vehicle_specialties_not_authored_again() -> None:
    """B207 keys Mechanic to a machine type, and #346 already recorded those."""
    derived = {
        label
        for family in MECHANIC_FAMILIES
        for _, label in VEHICLE_FAMILIES[family][5]
        # A muscle-powered hull carries no machinery for a Mechanic to work on.
        if label != "Unpowered"
    }
    mechanic = PROCEDURES["skill:mechanic"]
    assert {
        PROCEDURES[child].name.removeprefix("Mechanic (").removesuffix(")")
        for child in mechanic.specialties
    } == derived
    # Shiphandling is a command skill, so it contributes no machine type at all.
    assert "skill:mechanic-ship" not in PROCEDURES
    assert "skill:mechanic-unpowered" not in PROCEDURES
    assert PROCEDURES["skill:mechanic-motorboat"].dispatch == "object.repair"
    # Repairing a machine needs no vehicle-movement capability of its own.
    assert require_task(PROFILE, "skill:mechanic-motorboat").activation_blockers == ()
    assert require_task(PROFILE, "skill:boating-motorboat").activation_blockers == (
        "gurps.vehicles.movement",
    )


def test_the_two_electronics_rows_share_their_families_except_computers() -> None:
    """B184 Computer Operation is the skill that uses a computer, not B189."""
    operation = {
        PROCEDURES[c].specialty for c in PROCEDURES["skill:electronics-operation"].specialties
    }
    repair = {PROCEDURES[c].specialty for c in PROCEDURES["skill:electronics-repair"].specialties}
    assert {s.name for s in operation if s} == {name for name, _ in ELECTRONICS_SPECIALTIES}
    assert {s.name for s in repair if s} == {name for name, _ in REPAIRABLE_ELECTRONICS}
    assert {s.name for s in repair if s} - {s.name for s in operation if s} == {"computers"}


def test_an_open_family_records_the_axis_the_player_names() -> None:
    """B180/B187/B198: the subject is campaign data, so no list can enumerate it."""
    rows = {entry.id: entry for entry in inventory()}
    for identifier in sorted(OPEN):
        entry, procedure = rows[identifier], PROCEDURES[identifier]
        assert entry.variable is not None
        assert entry.variable.determination == "chosen-with-subject"
        assert entry.variable.subject == procedure.open_subject
        # The numbers are fixed whichever subject is named, so they stay on the
        # row itself and the open family does not repeat them.
        assert entry.variable.attribute is None and entry.variable.difficulty is None
        assert entry.definition is not None and entry.definition.skill is not None
        assert procedure.spec() == entry.definition.skill
        assert not procedure.specialties and not procedure.dispatchable
    assert {family: subject[4] for family, subject in OPEN_FAMILIES.items()} == {
        "biology": "one planet type",
        "disguise": "one species or culture",
        "geography": "one world or region",
        "geology": "one planet type",
    }


def test_every_expanded_specialty_is_indexed_under_its_own_family() -> None:
    """A new row is only accounted for while the source index carries it too."""
    index = {entry.id: entry for entry in source_index().entries}
    for family in (*SCIENCE_FAMILIES, "mechanic"):
        parent = PROCEDURES[f"skill:{family}"]
        for child in parent.specialties:
            row = index[child.removeprefix("skill:")]
            assert row.kind == "expansion"
            assert row.parent == family
            assert row.page == parent.page
    # An open family is one listed skill, not an expansion of anything.
    for identifier in OPEN:
        assert index[identifier.removeprefix("skill:")].kind == "skill"
