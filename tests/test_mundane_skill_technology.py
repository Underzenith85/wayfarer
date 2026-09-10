"""Technology, science and vehicle skill procedures (#346).

Expected targets, margins, outcomes and unit counts come from
``tests/fixtures/gurps/mundane_skill_technology.json``, which was written from the
source rules rather than generated from the services under test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Modifier, ModifierKind, RecordedDice
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.rules.mundane_skills import (
    PROFILE,
    SkillAudit,
    audit_report,
    inventory,
    require_available,
    unsupported_scope,
    validate_procedures,
)
from wayfarer.rules.mundane_skills import technology as tech

FIXTURE = Path("tests/fixtures/gurps/mundane_skill_technology.json")

# The exact inventory scope named by #346, verbatim and in full.
SCOPE = (
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
# The concrete open issues every unimplemented scoped row is transferred to.
CHILD_BLOCKERS = {338, 356}


Case = dict[str, object]


def load_cases() -> list[Case]:
    data = json.loads(FIXTURE.read_text())
    assert data["baseline_id"] == BASELINE_ID
    assert data["profile"] == PROFILE
    return cast(list[Case], data["cases"])


def field(case: Case, name: str) -> Case:
    return cast(Case, case[name])


def definitions() -> dict[str, SkillAudit]:
    return {entry.id: entry for entry in inventory()}


def operator_and_task(case: Case) -> tuple[tech.Operator, tech.Task]:
    given = field(case, "input")
    parent = given.get("parent_level")
    operator = tech.Operator(
        cast(str, case["skill_id"]),
        cast(int, given["level"]),
        cast(int, given["operator_tl"]),
        frozenset(cast(list[str], given.get("trained", []))),
        cast(int, parent) if parent is not None else None,
    )
    return operator, tech.Task(
        cast(int, given["task_tl"]), cast(bool, given["familiar"]), cast(int, given["handling"])
    )


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: str(case["id"]))
def test_procedure_cases_match_hand_entered_expectations(case: Case) -> None:
    entry = definitions()[cast(str, case["skill_id"])]
    assert entry.definition is not None
    operator, task = operator_and_task(case)
    dice = cast(list[int], field(case, "input")["dice"])
    result = tech.attempt(entry.definition, operator, task, rng=RecordedDice(dice))
    expected = field(case, "expected")
    assert result.procedure_id == expected["procedure"]
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
    assert tech.replay(entry.definition, result) == result


def test_every_case_binds_a_source_reference_and_a_scoped_row() -> None:
    cases = load_cases()
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert cast(str, case["reference"]).startswith("B")
        assert case["skill_id"] in definitions()
    # Every dispatch target and effect the procedure catalog declares is exercised.
    covered = {field(case, "expected")["dispatch"] for case in cases}
    assert covered == {member.value for member in tech.Dispatch}
    effects = {field(case, "expected")["effect"] for case in cases}
    assert effects == {member.value for member in tech.Effect}
    outcomes = {field(case, "expected")["outcome"] for case in cases}
    assert outcomes == {"critical-success", "success", "failure", "critical-failure"}


def test_the_named_scope_is_implemented_or_transferred_to_an_open_child() -> None:
    """#346 acceptance: no scoped row is silently left without an owner."""
    entries = definitions()
    for name in SCOPE:
        entry = entries[f"skill:{name}"]
        if entry.procedure is not None:
            assert entry.implementation == "implemented"
            assert tech.OWNER in entry.followup_issues
        else:
            # The row's own procedure owner is the concrete open child that must
            # implement it, not this issue.
            assert entry.procedure_owner in CHILD_BLOCKERS, entry.id
            assert entry.procedure_owner in entry.followup_issues
        # Nothing in this group may activate while its source review is open.
        assert not entry.available
        assert "first-printing-delta-audit" in entry.blockers


def test_families_are_expanded_into_concrete_specialties() -> None:
    """B180-B223: an implemented family must have real specialties, not a promise."""
    entries = definitions()
    families: dict[str, set[str]] = {}
    for entry in entries.values():
        spec = entry.definition.skill if entry.definition else None
        if spec is not None and spec.specialty is not None:
            families.setdefault(spec.specialty.family, set()).add(spec.specialty.name)
    assert {"large-powerboat", "motorboat", "sailboat", "unpowered"} == families["boating"]
    assert {"automobile", "motorcycle", "tracked"} <= families["driving"]
    assert {"light-airplane", "vertol", "aerospace"} <= families["piloting"]
    assert {"free-flooding", "large", "mini"} == families["submarine"]
    assert {"airship", "ship", "starship", "submarine"} == families["shiphandling"]
    # B185 and B192 cross-reference rows are the family's specialties themselves.
    assert {"airshipman", "seamanship", "spacer", "submariner"} == families["crewman"]
    assert {"battlesuit", "diving-suit", "nbc-suit", "vacc-suit"} == families["environment-suit"]
    assert {"demolition", "fireworks", "underwater-demolition"} <= families["explosives"]


def test_techniques_carry_parent_specific_defaults_and_caps() -> None:
    """B230: a technique is bounded by its own parent, never by a generic rule."""
    entries = definitions()
    expected = {
        "skill:motion-picture-camera": ("skill:photography", -2, 0),
        "skill:no-landing-extraction": ("skill:piloting", -5, 0),
        "skill:set-trap": ("skill:traps", -2, 0),
        "skill:work-by-touch": ("skill:traps", -5, 0),
    }
    for identifier, (parent, default, maximum) in expected.items():
        spec = entries[identifier].definition
        assert spec is not None and spec.skill is not None
        technique = spec.skill.technique
        assert technique is not None
        assert (technique.parent, technique.default_modifier, technique.maximum_modifier) == (
            parent,
            default,
            maximum,
        )


@pytest.mark.parametrize("level", [8, 15])
def test_a_technique_outside_its_parent_range_is_rejected(level: int) -> None:
    entry = definitions()["skill:no-landing-extraction"]
    assert entry.definition is not None
    operator = tech.Operator("skill:no-landing-extraction", level, 8, parent_level=14)
    with pytest.raises(ValidationError, match="parent-specific range"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


def test_a_technique_without_its_parent_level_cannot_be_rolled() -> None:
    entry = definitions()["skill:work-by-touch"]
    assert entry.definition is not None
    operator = tech.Operator("skill:work-by-touch", 10, 8)
    with pytest.raises(ValidationError, match="parent skill level"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


@pytest.mark.parametrize("identifier", ["skill:mechanic", "skill:motion-picture-camera"])
def test_a_row_without_a_procedure_fails_closed(identifier: str) -> None:
    """A transferred row is refused by name; it never falls back to a generic roll."""
    entry = definitions()[identifier]
    assert entry.definition is not None
    assert entry.procedure is None
    operator = tech.Operator(identifier, 12, 8, parent_level=14)
    with pytest.raises(ValidationError, match="No implemented procedure"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


def test_a_row_without_recorded_mechanics_cannot_be_rolled() -> None:
    """A listing-only row has nothing to roll against and says so by name."""
    entry = definitions()["skill:research"]
    assert entry.definition is not None
    listing = replace(entry.definition, skill=None)
    operator = tech.Operator("skill:research", 12, 8)
    with pytest.raises(ValidationError, match="no mechanics"):
        tech.attempt(listing, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


def test_a_non_positive_effective_skill_is_refused_before_any_die_is_drawn() -> None:
    entry = definitions()["skill:research"]
    assert entry.definition is not None
    operator = tech.Operator("skill:research", 0, 8)
    with pytest.raises(ValidationError, match="must be positive"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([]))


def test_an_untrained_prerequisite_refuses_the_attempt() -> None:
    """B179: Astronomy is not rollable without its trained Mathematics prerequisite."""
    entry = definitions()["skill:astronomy"]
    assert entry.definition is not None
    operator = tech.Operator("skill:astronomy", 13, 8)
    with pytest.raises(ValidationError, match="Untrained prerequisite"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


def test_an_operator_cannot_roll_somebody_elses_skill() -> None:
    entry = definitions()["skill:research"]
    assert entry.definition is not None
    operator = tech.Operator("skill:cryptography", 13, 8)
    with pytest.raises(ValidationError, match="does not match"):
        tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 3]))


def test_handling_is_rejected_where_the_procedure_does_not_steer() -> None:
    """B185: a Crewman holds a station; Handling belongs to whoever is steering."""
    entry = definitions()["skill:research"]
    assert entry.definition is not None
    operator = tech.Operator("skill:research", 13, 8)
    with pytest.raises(ValidationError, match="Handling does not apply"):
        tech.attempt(
            entry.definition, operator, tech.Task(8, handling=2), rng=RecordedDice([3, 3, 3])
        )


def test_procedures_fail_closed_outside_the_selected_profile() -> None:
    entry = definitions()["skill:research"]
    assert entry.definition is not None
    operator = tech.Operator("skill:research", 13, 8)
    with pytest.raises(ValidationError, match="Unknown rules profile"):
        tech.attempt(
            entry.definition,
            operator,
            tech.Task(8),
            rng=RecordedDice([3, 3, 3]),
            profile_id="gurps-invented-profile",
        )


def test_situational_modifiers_stay_typed_and_visible_in_the_receipt() -> None:
    """A caller-supplied ruling is recorded as its own modifier, never folded in."""
    entry = definitions()["skill:forensics"]
    assert entry.definition is not None
    operator = tech.Operator("skill:forensics", 14, 8)
    ruling = Modifier(-3, "contaminated-scene", PROFILE, BASELINE_ID, ModifierKind.SITUATIONAL)
    result = tech.attempt(
        entry.definition,
        operator,
        tech.Task(6, situational=(ruling,)),
        rng=RecordedDice([3, 3, 3]),
    )
    reasons = [(m.reason, m.value, m.kind.value) for m in result.check.modifiers]
    assert reasons == [
        ("technology-level-difference", -2, "situational"),
        ("contaminated-scene", -3, "situational"),
    ]
    assert result.check.effective_target == 9


def test_a_replayed_attempt_cannot_be_attributed_to_another_row() -> None:
    entries = definitions()
    entry = entries["skill:research"]
    other = entries["skill:cryptography"]
    assert entry.definition is not None and other.definition is not None
    operator = tech.Operator("skill:research", 13, 8)
    result = tech.attempt(entry.definition, operator, tech.Task(8), rng=RecordedDice([3, 3, 4]))
    with pytest.raises(ValidationError, match="does not match"):
        tech.replay(other.definition, result)


def test_unsupported_scope_names_owners_for_every_unplayable_row() -> None:
    rows = {row["id"]: row for row in unsupported_scope()}
    assert len(rows) == len(inventory())
    driving = rows["skill:driving-automobile"]
    assert driving["implementation"] == "implemented"
    assert driving["procedure"] == "procedure:driving"
    # The implemented procedure still names the capability row gating activation,
    # and that blocker is owned by the child that must verify it.
    owners = cast(dict[str, tuple[int, ...]], driving["blocker_owners"])
    assert "capability:gurps.vehicles.movement" in cast(list[str], driving["blockers"])
    assert owners["capability:gurps.vehicles.movement"] == (tech.CAPABILITY_OWNER,)
    mechanic = rows["skill:mechanic"]
    assert mechanic["implementation"] == "unsupported"
    assert mechanic["procedure"] is None
    assert cast(dict[str, tuple[int, ...]], mechanic["blocker_owners"])["runtime-procedure"] == (
        356,
    )


def test_activation_blockers_track_the_capability_registry() -> None:
    """Vehicle procedures are gated; procedures that need nothing extra are not."""
    assert tech.activation_blockers(tech.PROCEDURES["skill:driving"]) == (
        "gurps.vehicles.movement",
    )
    assert tech.activation_blockers(tech.PROCEDURES["skill:research"]) == ()
    for entry in tech.PROCEDURES.values():
        assert entry.id.startswith("procedure:")
        assert 168 <= entry.page <= 233
        assert entry.reference == f"B{entry.page}"


def test_a_procedure_for_a_row_outside_the_inventory_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tech, "implemented_rows", lambda: frozenset({"skill:invented"}))
    with pytest.raises(ValidationError, match="outside the inventory"):
        validate_procedures(inventory())


def test_an_unexpanded_family_cannot_report_itself_implemented() -> None:
    """Dropping every specialty of Driving must fail, not silently narrow the family."""
    entries = tuple(entry for entry in inventory() if not entry.id.startswith("skill:driving-"))
    with pytest.raises(ValidationError, match="still unexpanded"):
        validate_procedures(entries)


def test_an_implemented_row_still_cannot_be_activated() -> None:
    for identifier in ("skill:driving-automobile", "skill:vacc-suit", "skill:research"):
        with pytest.raises(ValidationError, match="unavailable"):
            require_available(identifier)


def test_the_audit_report_publishes_the_implemented_procedures() -> None:
    report = audit_report()
    procedures = cast(list[dict[str, object]], report["procedures"])
    assert report["implemented"] == len(procedures)
    assert {row["dispatch"] for row in procedures} == {member.value for member in tech.Dispatch}
    assert {"skill:vacc-suit", "skill:driving-automobile", "skill:set-trap"} <= {
        cast(str, row["id"]) for row in procedures
    }
    assert report["available"] == 0
