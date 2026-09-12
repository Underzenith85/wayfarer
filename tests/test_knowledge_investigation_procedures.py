"""Knowledge, academic and investigation skill procedures (#341)."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.knowledge import PROCEDURES, definitions
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


def test_all_twenty_seven_rows_are_bound_and_dispatchable() -> None:
    rows = [row for row in inventory() if row.procedure_owner == 341]
    assert len(rows) == 27
    assert all(row.bound and not row.blockers for row in rows)
    assert all(row.implementation == "implemented" for row in rows)
    assert {definition.id for definition in definitions()} == {row.id for row in rows}


@pytest.mark.parametrize(
    ("identifier", "reference", "dispatch", "effect"),
    (
        ("skill:accounting", "B174", "campaign.administration", "audit-accounts"),
        ("skill:law", "B204", "campaign.law", "interpret-applicable-law"),
        ("skill:history", "B200", "campaign.knowledge", "interpret-historical-evidence"),
        ("skill:detect-lies", "B187", "social.skill-procedure", "detect-deception"),
        ("skill:observation", "B211", "noncombat.investigation", "notice-deliberate-detail"),
        ("skill:stealth", "B222", "movement.approach", "move-without-detection"),
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


def test_every_procedure_executes_and_replays() -> None:
    for identifier, procedure in PROCEDURES.items():
        task = procedure.task
        assert task is not None
        dice = [3, 3, 3, 6, 6, 6]
        result = attempt(
            PROCEDURES,
            Performer(identifier, 12),
            situation(identifier),
            rng=RecordedDice(dice),
        )
        assert result.succeeded and result.effect == task.effect
        assert replay(result) == result


def test_subject_context_retry_policy_and_modifiers_fail_closed() -> None:
    with pytest.raises(ValidationError, match="subject-selected"):
        attempt(
            PROCEDURES,
            Performer("skill:law", 12),
            Situation(frozenset({"jurisdiction", "legal-question"})),
            rng=RecordedDice([3, 3, 3]),
        )
    search = PROCEDURES["skill:search"].task
    assert search is not None
    assert search.policy is RepeatedAttemptPolicy.UNKNOWN_UNTIL_LATER
    with pytest.raises(ValidationError, match="Modifier does not apply"):
        attempt(
            PROCEDURES,
            Performer("skill:search", 12),
            Situation(frozenset({"search-area"}), modifiers={"market-volatility": 1}),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="positive resistance"):
        attempt(
            PROCEDURES,
            Performer("skill:observation", 12),
            Situation(frozenset({"scene-present"})),
            rng=RecordedDice([3, 3, 3]),
        )


def test_capability_is_registered_as_partial() -> None:
    declared = CAPABILITIES["gurps.skills.knowledge_investigation"]
    assert declared.status is CoverageStatus.PARTIAL and declared.owner_issue == 341
