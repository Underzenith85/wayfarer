"""B290-B294 character-development expected-result fixtures for #499."""

import pytest
from test_actions import engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.administration import (
    ActivityAllocation,
    AdministrationState,
    TimeCredit,
    TimeUseEntry,
)
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.campaign.development import (
    AdvancementActorProfile,
    AdventureImprovementRule,
    DevelopmentRules,
    DevelopmentState,
    QuickLearningRule,
    ResolveQuickLearning,
    SettleAdventureImprovement,
    SettleStudy,
    StudyRule,
    TeachingBinding,
    apply_development,
    bind_teaching_outcome,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.builds import spendable_points


def profile(
    actor_id: str,
    *,
    iq: int = 10,
    teaching: int = 0,
    subject: int = 10,
    subject_points: int = 1,
    purchased_ids: tuple[str, ...] = (),
) -> AdvancementActorProfile:
    return AdvancementActorProfile(
        actor_id=actor_id,
        levels=(
            ("attribute:iq", iq),
            ("skill:teaching", teaching),
            ("skill:survival", subject),
        ),
        points=(("skill:survival", subject_points),),
        purchased_ids=purchased_ids,
    )


def time_use(identifier: str, actor_id: str, hours: int, days: int) -> TimeUseEntry:
    return TimeUseEntry(
        id=identifier,
        actor_id=actor_id,
        allocations=(ActivityAllocation(activity_id="survival-books", start=0, end=days * 86_400),),
        credits=(TimeCredit(activity_id="survival-books", kind="study", seconds=hours * 3600),),
        settled_at=days * 86_400,
    )


def apply(
    state: DevelopmentState,
    resources: ResourceState,
    administration: AdministrationState,
    advancement: tuple[AdvancementEntry, ...],
    command: SettleAdventureImprovement | SettleStudy | ResolveQuickLearning,
    rules: DevelopmentRules,
    profiles: dict[str, AdvancementActorProfile],
    dice: tuple[int, ...] = (1, 1, 1),
) -> tuple[DevelopmentState, ResourceState, tuple[AdvancementEntry, ...]]:
    developed, updated, ledger, _ = apply_development(
        state,
        resources,
        administration,
        advancement,
        command,
        rules,
        profiles=profiles,
        rng=RecordedDice(dice),
        build_revision="build-a",
        system=True,
    )
    return developed, updated, ledger


def test_adventure_award_and_gained_trait_keep_source_and_approval_boundary() -> None:
    rules = DevelopmentRules(
        id="development",
        version=1,
        adventures=(
            AdventureImprovementRule(
                id="jungle-trek",
                actor_id="a",
                points=2,
                eligible_definition_ids=("skill:hiking", "skill:survival"),
                gained_trait_ids=("trait:patron-expedition",),
                reason="Completed the expedition",
            ),
        ),
    )
    command = SettleAdventureImprovement(
        id="finish-trek", actor_id="a", expected_revision=0, rule_id="jungle-trek"
    )
    developed, resources, ledger = apply(
        DevelopmentState(),
        ResourceState(),
        AdministrationState(),
        (),
        command,
        rules,
        {"a": profile("a")},
    )
    assert ledger[0].points == 2
    assert ledger[0].eligible_definition_ids == ("skill:hiking", "skill:survival")
    assert ledger[0].source_kind == "adventure" and ledger[0].source_id == "jungle-trek"
    assert developed.permissions[0].definition_id == "trait:patron-expedition"
    assert developed.permissions[0].approval_required is True

    replayed = apply(
        developed,
        resources,
        AdministrationState(),
        ledger,
        command,
        rules,
        {"a": profile("a")},
    )
    assert replayed == (developed, resources, ledger)
    with pytest.raises(ConflictError, match="already settled"):
        apply(
            developed,
            resources,
            AdministrationState(),
            ledger,
            command.model_copy(update={"id": "finish-again", "expected_revision": 1}),
            rules,
            {"a": profile("a")},
        )


def test_interrupted_self_study_retains_hours_and_settles_each_time_use_once() -> None:
    rules = DevelopmentRules(
        id="development",
        version=1,
        study=(
            StudyRule(
                id="survival-self-study",
                activity_id="survival-books",
                subject_id="skill:survival",
                method="self-study",
            ),
        ),
    )
    first, second = time_use("first", "a", 398, 34), time_use("second", "a", 2, 1)
    administration = AdministrationState(time_use=(first, second))
    developed, resources, ledger = apply(
        DevelopmentState(),
        ResourceState(),
        administration,
        (),
        SettleStudy(
            id="study-first",
            actor_id="a",
            expected_revision=0,
            rule_id="survival-self-study",
            time_use_id="first",
        ),
        rules,
        {"a": profile("a")},
    )
    assert developed.progress[0].learning_seconds == 199 * 3600
    assert ledger == ()
    developed, resources, ledger = apply(
        developed,
        resources,
        administration,
        ledger,
        SettleStudy(
            id="study-second",
            actor_id="a",
            expected_revision=1,
            rule_id="survival-self-study",
            time_use_id="second",
        ),
        rules,
        {"a": profile("a")},
    )
    assert developed.progress[0].learning_seconds == 200 * 3600
    assert ledger[0].points == 1 and ledger[0].eligible_definition_ids == ("skill:survival",)
    with pytest.raises(ConflictError, match="already settled"):
        apply(
            developed,
            resources,
            administration,
            ledger,
            SettleStudy(
                id="study-second-again",
                actor_id="a",
                expected_revision=2,
                rule_id="survival-self-study",
                time_use_id="second",
            ),
            rules,
            {"a": profile("a")},
        )


def test_education_rejects_an_unqualified_teacher() -> None:
    rules = DevelopmentRules(
        id="development",
        version=1,
        study=(
            StudyRule(
                id="course",
                activity_id="survival-books",
                subject_id="skill:survival",
                method="education",
                teacher_id="teacher",
            ),
        ),
    )
    entry = time_use("course-time", "a", 1, 1)
    with pytest.raises(ValidationError, match="Teaching at level 12"):
        apply(
            DevelopmentState(),
            ResourceState(),
            AdministrationState(time_use=(entry,)),
            (),
            SettleStudy(
                id="course-settle",
                actor_id="a",
                expected_revision=0,
                rule_id="course",
                time_use_id="course-time",
            ),
            rules,
            {"a": profile("a"), "teacher": profile("teacher", teaching=11, subject=14)},
        )


def test_successful_teaching_lesson_is_consumed_by_the_students_study_command() -> None:
    rules = DevelopmentRules(
        id="development",
        version=1,
        study=(
            StudyRule(
                id="course",
                activity_id="survival-books",
                subject_id="skill:survival",
                method="education",
                teacher_id="teacher",
            ),
        ),
        teaching=(
            TeachingBinding(id="lesson", trigger_id="lesson-trigger", study_rule_id="course"),
        ),
    )
    entry = time_use("course-time", "student", 8, 1)
    command = SettleStudy(
        id="settle-lesson",
        actor_id="student",
        expected_revision=0,
        rule_id="course",
        time_use_id=entry.id,
    )
    with pytest.raises(ValidationError, match="successful Teaching lesson"):
        apply(
            DevelopmentState(),
            ResourceState(),
            AdministrationState(time_use=(entry,)),
            (),
            command,
            rules,
            {
                "student": profile("student"),
                "teacher": profile("teacher", teaching=12, subject=12),
            },
        )

    taught = bind_teaching_outcome(
        DevelopmentState(),
        rules,
        command_id="teaching-roll",
        trigger_id="lesson-trigger",
        teacher_id="teacher",
        student_id="student",
        outcome="teaching-taught",
    )
    developed, _, _ = apply(
        taught,
        ResourceState(),
        AdministrationState(time_use=(entry,)),
        (),
        command,
        rules,
        {
            "student": profile("student"),
            "teacher": profile("teacher", teaching=12, subject=12),
        },
    )
    assert developed.consumed_lesson_ids == ("teaching-roll",)
    assert developed.progress[0].learning_seconds == 8 * 3600


def test_quick_learning_requires_a_stressful_default_and_prior_award() -> None:
    earned = AdvancementEntry(
        id="previous-session",
        actor_id="a",
        kind="earned",
        points=1,
        revision=1,
        build_before="build-a",
        build_after="build-a",
        reason="Previous session award",
    )
    rules = DevelopmentRules(
        id="development",
        version=1,
        quick_learning=(
            QuickLearningRule(
                id="field-medicine-default",
                actor_id="a",
                skill_id="skill:first-aid",
                stressful_event_id="stabilized-under-fire",
                earned_entry_id=earned.id,
            ),
        ),
    )
    developed, _, _ = apply(
        DevelopmentState(),
        ResourceState(revision=1),
        AdministrationState(),
        (earned,),
        ResolveQuickLearning(
            id="learn-first-aid",
            actor_id="a",
            expected_revision=1,
            rule_id="field-medicine-default",
        ),
        rules,
        {"a": profile("a", purchased_ids=("trait:eidetic-memory",))},
    )
    assert developed.quick_learning[0].succeeded is True
    assert developed.permissions[0].definition_id == "skill:first-aid"


def test_source_limited_points_cannot_fund_an_unrelated_revision() -> None:
    state = seed(engine()).model_copy(
        update={
            "advancement": (
                AdvancementEntry(
                    id="study-credit",
                    actor_id="a",
                    kind="earned",
                    points=2,
                    revision=1,
                    build_before="build-a",
                    build_after="build-a",
                    reason="Survival study",
                    eligible_definition_ids=("skill:survival",),
                    source_kind="study",
                    source_id="survival-course",
                ),
                AdvancementEntry(
                    id="old-purchase",
                    actor_id="a",
                    kind="purchase",
                    points=-1,
                    revision=1,
                    build_before="build-a",
                    build_after="build-b",
                    reason="Prior discretionary spend",
                ),
            )
        }
    )
    assert spendable_points(state, "a", frozenset({"skill:survival"})) == 2
    assert spendable_points(state, "a", frozenset({"skill:diplomacy"})) == 0
