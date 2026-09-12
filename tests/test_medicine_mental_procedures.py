"""Medicine, psychology and mental discipline procedures (#342)."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.medicine import PROCEDURES, definitions
from wayfarer.engine.rules.skills.mundane.procedures import Performer, Situation, attempt, replay
from wayfarer.errors import ValidationError


def situation(identifier: str) -> Situation:
    procedure = PROCEDURES[identifier]
    task = procedure.task
    assert task is not None
    conditions = set(task.required_context)
    if procedure.subject is not None:
        conditions.add("subject-selected")
    return Situation(frozenset(conditions), resistance=10)


def test_all_twenty_two_rows_are_bound_and_implemented() -> None:
    rows = [row for row in inventory() if row.procedure_owner == 342]
    assert len(rows) == 22
    assert all(row.bound and not row.blockers for row in rows)
    assert all(row.implementation == "implemented" for row in rows)
    assert {definition.id for definition in definitions()} == {
        row.id for row in rows if row.id != "skill:pharmacy"
    }


def test_pharmacy_family_requires_one_of_its_source_specialties() -> None:
    pharmacy = PROCEDURES["skill:pharmacy"]
    assert pharmacy.specialties == (
        "skill:pharmacy-herbal",
        "skill:pharmacy-synthetic",
    )
    with pytest.raises(ValidationError, match="concrete specialty"):
        attempt(
            PROCEDURES,
            Performer("skill:pharmacy", 12),
            Situation(),
            rng=RecordedDice([3, 3, 3]),
        )


@pytest.mark.parametrize(
    ("identifier", "reference", "dispatch", "effect"),
    (
        ("skill:autohypnosis", "B179", "noncombat.mental-procedure", "induce-self-trance"),
        ("skill:diagnosis", "B187", "recovery.medical-treatment", "identify-condition"),
        ("skill:first-aid", "B195", "recovery.medical-treatment", "stabilize-injury"),
        ("skill:physiology", "B213", "campaign.knowledge", "analyze-species-physiology"),
        ("skill:scuba", "B219", "hazard.exposure", "operate-self-contained-breathing-gear"),
        ("skill:surgery", "B223", "recovery.medical-treatment", "perform-surgery"),
    ),
)
def test_selected_source_rows_have_distinct_contracts(
    identifier: str,
    reference: str,
    dispatch: str,
    effect: str,
) -> None:
    procedure = PROCEDURES[identifier]
    task = procedure.task
    assert task is not None
    assert (procedure.reference, procedure.dispatch, task.effect) == (reference, dispatch, effect)


def test_every_concrete_procedure_executes_and_replays() -> None:
    for identifier, procedure in PROCEDURES.items():
        if procedure.specialties:
            continue
        task = procedure.task
        assert task is not None
        result = attempt(
            PROCEDURES,
            Performer(identifier, 12),
            situation(identifier),
            rng=RecordedDice([3, 3, 3, 6, 6, 6]),
        )
        assert result.succeeded and result.effect == task.effect
        assert replay(result) == result


def test_subject_retry_context_modifiers_and_profile_fail_closed() -> None:
    with pytest.raises(ValidationError, match="subject-selected"):
        attempt(
            PROCEDURES,
            Performer("skill:physiology", 12),
            Situation(frozenset({"anatomical-question"})),
            rng=RecordedDice([3, 3, 3]),
        )
    diagnosis = PROCEDURES["skill:diagnosis"].task
    assert diagnosis is not None
    assert diagnosis.policy is RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER
    with pytest.raises(ValidationError, match="patient-and-symptoms"):
        attempt(
            PROCEDURES,
            Performer("skill:diagnosis", 12),
            Situation(),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="Modifier does not apply"):
        attempt(
            PROCEDURES,
            Performer("skill:first-aid", 12),
            Situation(frozenset({"injured-patient"}), modifiers={"market-volatility": 1}),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        attempt(
            PROCEDURES,
            Performer("skill:first-aid", 12),
            Situation(frozenset({"injured-patient"})),
            rng=RecordedDice([3, 3, 3]),
            profile_id="other-profile",
        )
    with pytest.raises(ValidationError, match="positive resistance"):
        attempt(
            PROCEDURES,
            Performer("skill:hypnotism", 12),
            Situation(frozenset({"attentive-subject"})),
            rng=RecordedDice([3, 3, 3]),
        )


def test_capability_is_registered_as_partial() -> None:
    declared = CAPABILITIES["gurps.skills.medicine_mental"]
    assert declared.status is CoverageStatus.PARTIAL and declared.owner_issue == 342
