"""Whole-entry arts, crafts and trade procedures (#338)."""

import json
from pathlib import Path

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.arts import PROCEDURES, definitions
from wayfarer.engine.rules.skills.mundane.arts_attempts import (
    Performer,
    Situation,
    attempt,
    replay,
)
from wayfarer.engine.rules.skills.mundane.procedures import Resolution
from wayfarer.errors import ValidationError

FIXTURE = Path("tests/fixtures/gurps/arts_skills.json")
LISTED = (
    "armoury artist bartender camouflage carpentry connoisseur cooking counterfeiting dancing "
    "farming filch forgery games heraldry hobby-skill holdout housekeeping jeweler "
    "leatherworking lockpicking machinist makeup masonry merchant mimicry musical-composition "
    "musical-instrument photography pickpocket poetry professional-skill prospecting sewing "
    "singing sleight-of-hand smith smuggling typing ventriloquism writing fire-eating impersonate "
    "group-performance"
).split()
FAMILIES = {
    "skill:armoury": 8,
    "skill:group-performance": 4,
    "skill:mimicry": 3,
    "skill:smith": 3,
}


def cases() -> list[dict[str, object]]:
    data: dict[str, object] = json.loads(FIXTURE.read_text())
    assert data["source"] == "Basic Set Characters, Fourth Edition, third printing, B178-B233"
    result = data["cases"]
    assert isinstance(result, list)
    return result


def test_every_listed_row_is_bound_or_a_recorded_open_family() -> None:
    entries = {row.id: row for row in inventory()}
    assert all(f"skill:{identifier}" in entries for identifier in LISTED)
    owned = [row for row in entries.values() if row.procedure_owner == 338]
    # #346 transferred Motion-Picture Camera once its Photography parent landed.
    assert len(owned) == 62
    assert all(row.bound for row in owned)
    assert {row.implementation for row in owned} == {"implemented", "contextual"}
    assert all(not row.blockers for row in owned)
    assert entries["skill:motion-picture-camera"].dispatch == "noncombat.approach"


def test_finite_families_require_their_recorded_concrete_specialty() -> None:
    for identifier, count in FAMILIES.items():
        procedure = PROCEDURES[identifier]
        assert len(procedure.specialties) == count
        assert procedure.implemented and not procedure.dispatchable
        with pytest.raises(ValidationError, match="concrete specialty"):
            attempt(Performer(identifier, 12), Situation(), rng=RecordedDice([3, 3, 3]))


@pytest.mark.parametrize("case", cases(), ids=lambda case: str(case["skill"]))
def test_source_referenced_expected_results(case: dict[str, object]) -> None:
    identifier = case["skill"]
    assert isinstance(identifier, str)
    context = case["context"]
    assert isinstance(context, list)
    procedure = PROCEDURES[identifier]
    assert procedure.task is not None
    required = frozenset(str(value) for value in context)
    spec = procedure.spec()
    technique = spec.technique if spec else None
    performer = Performer(
        identifier,
        14,
        parent_level=14 if procedure.template or technique else None,
        parent_skill_id=(
            "skill:acting" if procedure.template else technique.parent if technique else None
        ),
    )
    dice = (
        [3, 3, 3, 6, 6, 6] if procedure.task.resolution is Resolution.QUICK_CONTEST else [3, 3, 3]
    )
    result = attempt(
        performer,
        Situation(required, resistance=10),
        rng=RecordedDice(dice),
    )
    assert result.dispatch == case["dispatch"]
    assert result.effect == case["effect"]
    assert result.unit == case["unit"]
    assert procedure.task.resolution.value == case["resolution"]
    assert result.succeeded
    assert replay(result) == result


def test_every_concrete_row_executes_and_produces_a_replayable_receipt() -> None:
    for procedure in PROCEDURES.values():
        if not procedure.dispatchable:
            continue
        task = procedure.task
        assert task is not None
        conditions = set(task.required_context)
        if procedure.subject:
            conditions.add("subject-selected")
        spec = procedure.spec()
        technique = spec.technique if spec else None
        performer = Performer(
            procedure.id,
            12,
            12 if procedure.template or technique else None,
            procedure.template.parents[0]
            if procedure.template
            else technique.parent
            if technique
            else None,
        )
        rolls = [3, 3, 3, 6, 6, 6] if task.resolution is Resolution.QUICK_CONTEST else [3, 3, 3]
        result = attempt(
            performer,
            Situation(frozenset(conditions), resistance=10),
            rng=RecordedDice(rolls),
        )
        assert result.succeeded and result.dispatch == task.dispatch
        assert replay(result) == result


def test_context_modifiers_profile_and_technique_parent_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Missing procedure context"):
        attempt(Performer("skill:lockpicking", 12), Situation(), rng=RecordedDice([3, 3, 3]))
    with pytest.raises(ValidationError, match="Modifier does not apply"):
        attempt(
            Performer("skill:lockpicking", 12),
            Situation(frozenset({"lock-and-tools"}), modifiers={"audience": 1}),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        attempt(
            Performer("skill:lockpicking", 12),
            Situation(frozenset({"lock-and-tools"})),
            rng=RecordedDice([3, 3, 3]),
            profile_id="prototype",
        )
    with pytest.raises(ValidationError, match="parent does not match"):
        attempt(
            Performer("skill:impersonate", 12, 12, "skill:mimicry-speech"),
            Situation(frozenset({"identity-selected"}), resistance=10),
            rng=RecordedDice([3, 3, 3, 6, 6, 6]),
        )


def test_only_concrete_skill_rows_publish_definitions() -> None:
    published = definitions()
    assert published
    assert all(definition.status.value == "implemented" for definition in published)
    assert not {definition.id for definition in published} & set(FAMILIES)
    assert "skill:impersonate" not in {definition.id for definition in published}
    capability = CAPABILITIES["gurps.skills.arts_trades"]
    assert capability.status is CoverageStatus.PARTIAL and capability.owner_issue == 338
