"""Physical, outdoor and animal skill procedures (#343)."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.physical_outdoors import PROCEDURES, definitions
from wayfarer.engine.rules.skills.mundane.procedures import Performer, Situation, attempt, replay
from wayfarer.errors import ValidationError

FAMILIES = {"skill:meteorology", "skill:navigation", "skill:survival"}
PARENTS = {
    "skill:lifesaving": "skill:swimming",
    "skill:rope-up": "skill:climbing",
    "skill:scaling": "skill:climbing",
    "skill:slip-handcuffs": "skill:escape",
}


def situation(identifier: str) -> Situation:
    procedure = PROCEDURES[identifier]
    task = procedure.task
    assert task is not None
    conditions = set(task.required_context)
    if procedure.subject is not None:
        conditions.add("subject-selected")
    return Situation(frozenset(conditions), resistance=10)


def performer(identifier: str) -> Performer:
    parent = PARENTS.get(identifier)
    return Performer(identifier, 12, 12 if parent else None, parent)


def test_all_seventy_rows_are_bound_without_remaining_blockers() -> None:
    rows = [row for row in inventory() if row.procedure_owner == 343]
    assert len(rows) == 70
    assert all(row.bound and not row.blockers for row in rows)
    assert sum(row.implementation == "implemented" for row in rows) == 66
    assert sum(row.implementation == "contextual" for row in rows) == 4
    assert {definition.id for definition in definitions()} == {
        row.id for row in rows if row.id not in FAMILIES and row.implementation == "implemented"
    }


@pytest.mark.parametrize(
    ("identifier", "reference", "dispatch", "effect"),
    (
        ("skill:acrobatics", "B174", "movement.physical-procedure", "perform-acrobatic-maneuver"),
        ("skill:animal-handling", "B175", "movement.mount-operation", "handle-or-train-animal"),
        ("skill:climbing", "B183", "movement.physical-procedure", "scale-surface"),
        ("skill:hiking", "B200", "movement.approach", "complete-overland-march"),
        ("skill:meteorology-earthlike", "B209", "campaign.knowledge", "forecast-earthlike-weather"),
        ("skill:navigation-sea", "B211", "movement.approach", "plot-sea-route"),
        ("skill:survival-woodlands", "B223", "hazard.exposure", "survive-woodlands-environment"),
        ("skill:tracking", "B226", "noncombat.investigation", "follow-trail"),
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


def test_finite_families_require_a_concrete_specialty() -> None:
    assert PROCEDURES["skill:meteorology"].specialties == tuple(
        f"skill:meteorology-{name}"
        for name in (
            "earthlike",
            "gas-giants",
            "hostile-terrestrial",
            "ice-dwarfs",
            "ice-worlds",
            "rock-worlds",
        )
    )
    assert len(PROCEDURES["skill:navigation"].specialties) == 5
    assert len(PROCEDURES["skill:survival"].specialties) == 16
    for identifier in FAMILIES:
        with pytest.raises(ValidationError, match="concrete specialty"):
            attempt(
                PROCEDURES,
                Performer(identifier, 12),
                Situation(),
                rng=RecordedDice([3, 3, 3]),
            )


def test_every_dispatchable_procedure_executes_and_replays() -> None:
    for identifier, procedure in PROCEDURES.items():
        if procedure.specialties:
            continue
        task = procedure.task
        assert task is not None
        result = attempt(
            PROCEDURES,
            performer(identifier),
            situation(identifier),
            rng=RecordedDice([3, 3, 3, 6, 6, 6]),
        )
        assert result.succeeded and result.effect == task.effect
        assert replay(result) == result


def test_subject_technique_retry_contest_and_validation_fail_closed() -> None:
    with pytest.raises(ValidationError, match="subject-selected"):
        attempt(
            PROCEDURES,
            Performer("skill:animal-handling", 12),
            Situation(frozenset({"animal-present"})),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="explicit parent"):
        attempt(
            PROCEDURES,
            Performer("skill:lifesaving", 12),
            situation("skill:lifesaving"),
            rng=RecordedDice([3, 3, 3]),
        )
    tracking = PROCEDURES["skill:tracking"].task
    assert tracking is not None
    assert tracking.policy is RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER
    with pytest.raises(ValidationError, match="positive resistance"):
        attempt(
            PROCEDURES,
            Performer("skill:sports", 12),
            Situation(frozenset({"sporting-contest", "subject-selected"})),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="Modifier does not apply"):
        attempt(
            PROCEDURES,
            Performer("skill:climbing", 12),
            Situation(frozenset({"climb-route"}), modifiers={"market-volatility": 1}),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        attempt(
            PROCEDURES,
            Performer("skill:climbing", 12),
            Situation(frozenset({"climb-route"})),
            rng=RecordedDice([3, 3, 3]),
            profile_id="other-profile",
        )


def test_capability_is_registered_as_partial() -> None:
    declared = CAPABILITIES["gurps.skills.physical_outdoors"]
    assert declared.status is CoverageStatus.PARTIAL and declared.owner_issue == 343
