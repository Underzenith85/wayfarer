"""B292–293 learning conversion and intensive eligibility through actual settlement."""

from typing import Literal

import pytest
from test_character_development import apply, profile, time_use

from wayfarer.engine.simulation.campaign.administration import AdministrationState
from wayfarer.engine.simulation.campaign.development import (
    DevelopmentRules,
    DevelopmentState,
    SettleStudy,
    StudyRule,
    TeachingBinding,
    bind_teaching_outcome,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize(
    ("method", "hours", "days", "learning"),
    [
        ("self-study", 12, 1, 6),
        ("self-study", 24, 1, 6),
        ("self-study", 400, 34, 200),
        ("education", 8, 1, 8),
        ("education", 12, 1, 8),
        ("intensive", 16, 1, 32),
        ("intensive", 20, 1, 32),
        ("job", 2, 1, 2),
        ("job", 4, 1, 2),
        ("adventure", 16, 1, 16),
    ],
)
def test_learning_hours_settle_into_real_progress_and_point_ledger(
    method: Literal["self-study", "education", "intensive", "job", "adventure"],
    hours: int,
    days: int,
    learning: int,
) -> None:
    instructed = method in ("education", "intensive")
    rules = DevelopmentRules(
        id="source",
        version=1,
        study=(
            StudyRule(
                id="course",
                activity_id="survival-books",
                subject_id="skill:survival",
                method=method,
                teacher_id="teacher" if instructed else None,
            ),
        ),
        teaching=(TeachingBinding(id="lesson", trigger_id="lesson", study_rule_id="course"),)
        if instructed
        else (),
    )
    state = DevelopmentState()
    if instructed:
        state = bind_teaching_outcome(
            state,
            rules,
            command_id="lesson",
            trigger_id="lesson",
            teacher_id="teacher",
            student_id="a",
            outcome="teaching-taught",
        )
    student = profile("a").model_copy(
        update={"levels": profile("a").levels + (("attribute:ht", 12),)}
    )
    profiles = {
        "a": student,
        "teacher": profile("teacher", teaching=12, subject=14, subject_points=4),
    }
    administration = AdministrationState(time_use=(time_use("time", "a", hours, days),))
    command = SettleStudy(
        id="study", actor_id="a", expected_revision=0, rule_id="course", time_use_id="time"
    )
    developed, resources, ledger = apply(
        state, ResourceState(), administration, (), command, rules, profiles
    )
    assert developed.progress[0].learning_seconds == learning * 3600
    assert sum(entry.points for entry in ledger) == learning // 200
    restored = DevelopmentState.model_validate_json(developed.model_dump_json())
    assert apply(
        restored,
        ResourceState.model_validate_json(resources.model_dump_json()),
        administration,
        ledger,
        command,
        rules,
        profiles,
    ) == (restored, resources, ledger)
    with pytest.raises(ConflictError):
        apply(
            restored,
            resources,
            administration,
            ledger,
            command.model_copy(update={"id": "stale"}),
            rules,
            profiles,
        )


@pytest.mark.parametrize(
    ("ht", "traits", "allowed"),
    [
        (11, (), False),
        (12, (), True),
        (11, ("trait:fit",), True),
        (10, ("trait:fit",), False),
        (10, ("trait:very-fit",), True),
    ],
)
def test_intensive_training_requires_source_effective_health(
    ht: int,
    traits: tuple[str, ...],
    allowed: bool,
) -> None:
    rules = DevelopmentRules(
        id="source",
        version=1,
        study=(
            StudyRule(
                id="course",
                activity_id="survival-books",
                subject_id="skill:survival",
                method="intensive",
                teacher_id="teacher",
            ),
        ),
    )
    student = profile("a", purchased_ids=traits).model_copy(
        update={"levels": profile("a").levels + (("attribute:ht", ht),)}
    )
    args = (
        DevelopmentState(),
        ResourceState(),
        AdministrationState(time_use=(time_use("time", "a", 16, 1),)),
        (),
        SettleStudy(
            id="study", actor_id="a", expected_revision=0, rule_id="course", time_use_id="time"
        ),
        rules,
        {"a": student, "teacher": profile("teacher", teaching=12, subject=14, subject_points=4)},
    )
    if allowed:
        developed, _, _ = apply(*args)
        assert developed.progress[0].learning_seconds == 32 * 3600
    else:
        with pytest.raises(ValidationError, match="effective HT 12"):
            apply(*args)


def test_living_cost_source_status_zero_and_authored_dependent_conserve_money() -> None:
    from test_economics import apply as settle
    from test_economics import balances, configured, state

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.campaign.economics import PayCostOfLiving

    reducer = configured()
    seed = state(reducer)
    # B265 Status 0 costs $600; this campaign authors $100 for dependents.
    command = PayCostOfLiving(id="living", actor_id="a", expected_revision=0, period_id="month")
    before = balances(seed)
    updated, outcome = settle(reducer, seed, command, ())
    assert outcome.amount == 700
    assert balances(updated)["a-cash"] == before["a-cash"] - 700
    assert balances(updated)["b-cash"] == before["b-cash"] + 700
    restored = PlayState.model_validate_json(updated.model_dump_json())
    assert settle(reducer, restored, command, ()) == (restored, outcome)
    with pytest.raises(ConflictError):
        settle(reducer, restored, command.model_copy(update={"id": "stale"}), ())
