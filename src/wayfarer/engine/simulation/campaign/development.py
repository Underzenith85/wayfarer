"""Source-bound character development procedures (Basic Set B290-B294)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.campaign.administration import AdministrationState, TimeUseEntry
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

LEARNING_SECONDS_PER_POINT = 200 * 60 * 60


class AdventureImprovementRule(Record):
    id: Id
    actor_id: Id
    points: int = Field(ge=0, le=10000)
    eligible_definition_ids: tuple[Id, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    gained_trait_ids: tuple[Id, ...] = ()


class StudyRule(Record):
    id: Id
    activity_id: Id
    subject_id: Id
    method: Literal["self-study", "education", "intensive", "job", "adventure"]
    teacher_id: Id | None = None
    learnable_advantage: bool = False
    access_id: Id | None = None
    campaign_permission: bool = False

    @model_validator(mode="after")
    def valid_method(self) -> StudyRule:
        instructed = self.method in ("education", "intensive")
        if instructed != (self.teacher_id is not None):
            raise ValueError("Instruction study must name exactly one teacher")
        if self.learnable_advantage and (not self.campaign_permission or self.access_id is None):
            raise ValueError("Learnable advantage study requires permission and access")
        return self


class QuickLearningRule(Record):
    id: Id
    actor_id: Id
    skill_id: Id
    stressful_event_id: Id
    earned_entry_id: Id
    default_available: bool = True


class DevelopmentRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    adventures: tuple[AdventureImprovementRule, ...] = ()
    study: tuple[StudyRule, ...] = ()
    quick_learning: tuple[QuickLearningRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> DevelopmentRules:
        identifiers = tuple(value.id for value in self.adventures)
        identifiers += tuple(value.id for value in self.study)
        identifiers += tuple(value.id for value in self.quick_learning)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Duplicate character-development rule ID")
        if len({value.activity_id for value in self.study}) != len(self.study):
            raise ValueError("A Time Use activity may settle only one study subject")
        return self


class AdvancementActorProfile(Record):
    actor_id: Id
    levels: tuple[tuple[Id, int], ...] = ()
    points: tuple[tuple[Id, int], ...] = ()
    purchased_ids: tuple[Id, ...] = ()


class StudyProgress(Record):
    actor_id: Id
    subject_id: Id
    learning_seconds: int = Field(ge=0)
    points_awarded: int = Field(ge=0)


class AdvancementPermission(Record):
    id: Id
    actor_id: Id
    definition_id: Id
    source_kind: Literal["gained-in-play", "quick-learning", "study"]
    source_id: Id
    approval_required: bool = True


class QuickLearningAttempt(Record):
    id: Id
    rule_id: Id
    actor_id: Id
    succeeded: bool


class DevelopmentState(Record):
    progress: tuple[StudyProgress, ...] = ()
    consumed_time_use_ids: tuple[Id, ...] = ()
    settled_adventure_ids: tuple[Id, ...] = ()
    permissions: tuple[AdvancementPermission, ...] = ()
    quick_learning: tuple[QuickLearningAttempt, ...] = ()


class DevelopmentCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class SettleAdventureImprovement(DevelopmentCommand):
    kind: Literal["adventure-improvement"] = "adventure-improvement"
    rule_id: Id


class SettleStudy(DevelopmentCommand):
    kind: Literal["study-settlement"] = "study-settlement"
    rule_id: Id
    time_use_id: Id


class ResolveQuickLearning(DevelopmentCommand):
    kind: Literal["quick-learning"] = "quick-learning"
    rule_id: Id


CharacterDevelopmentCommand = Annotated[
    SettleAdventureImprovement | SettleStudy | ResolveQuickLearning,
    Field(discriminator="kind"),
]
DEVELOPMENT_COMMAND_ADAPTER: TypeAdapter[CharacterDevelopmentCommand] = TypeAdapter(
    CharacterDevelopmentCommand
)


class DevelopmentOutcome(Record):
    status: Literal["awarded", "studied", "learned", "not-learned"]
    points: int = Field(default=0, ge=0)
    learning_seconds: int = Field(default=0, ge=0)
    private: str = ""
    consequence: str | None = None


def _digest(command: DevelopmentCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: DevelopmentCommand) -> DevelopmentOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Character-development command ID reused")
    event = next(item for item in resources.events if item.id == "development:" + command.id)
    return DevelopmentOutcome.model_validate_json(event.kind)


class DevelopmentAuthoredRule(Protocol):
    id: str


def _rule[RuleT: DevelopmentAuthoredRule](
    values: tuple[RuleT, ...], identifier: str, label: str
) -> RuleT:
    result = next((value for value in values if value.id == identifier), None)
    if result is None:
        raise ValidationError(f"Unknown authored {label}")
    return result


def _profile(
    profiles: Mapping[str, AdvancementActorProfile], actor_id: str
) -> AdvancementActorProfile:
    result = profiles.get(actor_id)
    if result is None:
        raise ValidationError("Character development requires a valid actor build")
    return result


def _instruction_valid(
    rule: StudyRule, profiles: Mapping[str, AdvancementActorProfile], actor_id: str
) -> None:
    if rule.teacher_id is None:
        return
    teacher = _profile(profiles, rule.teacher_id)
    student = _profile(profiles, actor_id)
    teacher_levels, student_levels = dict(teacher.levels), dict(student.levels)
    teacher_points, student_points = dict(teacher.points), dict(student.points)
    if teacher_levels.get("skill:teaching", 0) < 12:
        raise ValidationError("Teacher requires Teaching at level 12 or better")
    if not (
        teacher_levels.get(rule.subject_id, 0) >= student_levels.get(rule.subject_id, 0)
        or teacher_points.get(rule.subject_id, 0) >= student_points.get(rule.subject_id, 0)
    ):
        raise ValidationError("Teacher is not sufficiently accomplished in the subject")
    if rule.method == "intensive" and not (
        teacher_levels.get(rule.subject_id, 0) > student_levels.get(rule.subject_id, 0)
        and teacher_points.get(rule.subject_id, 0) > student_points.get(rule.subject_id, 0)
    ):
        raise ValidationError("Intensive training requires an expert teacher")


def _study_credit(rule: StudyRule, entry: TimeUseEntry) -> int:
    credit = sum(
        item.seconds
        for item in entry.credits
        if item.activity_id == rule.activity_id and item.kind == "study"
    )
    if credit <= 0:
        raise ValidationError("Time Use entry has no study credit for this program")
    numerator, denominator = {
        "self-study": (1, 2),
        "education": (1, 1),
        "intensive": (2, 1),
        # Job activities and adventuring already author their B293 conversion
        # when Time Use credit is produced.
        "job": (1, 1),
        "adventure": (1, 1),
    }[rule.method]
    learning = credit * numerator // denominator
    elapsed = max(item.end for item in entry.allocations) - min(
        item.start for item in entry.allocations
    )
    days = max(1, (elapsed + 86_399) // 86_400)
    daily_learning_cap = {
        "self-study": 6 * 3600,
        "education": 8 * 3600,
        "intensive": 32 * 3600,
        "job": 2 * 3600,
        "adventure": learning,
    }[rule.method]
    return min(learning, daily_learning_cap * days)


def _finish(
    resources: ResourceState,
    command: DevelopmentCommand,
    outcome: DevelopmentOutcome,
) -> ResourceState:
    revision = resources.revision + 1
    return resources.model_copy(
        update={
            "revision": revision,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="development:" + command.id,
                    at=resources.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )


def apply_development(
    state: DevelopmentState,
    resources: ResourceState,
    administration: AdministrationState,
    advancement: tuple[AdvancementEntry, ...],
    command: CharacterDevelopmentCommand,
    rules: DevelopmentRules,
    *,
    profiles: Mapping[str, AdvancementActorProfile],
    rng: RandomSource,
    build_revision: str,
    system: bool = False,
) -> tuple[DevelopmentState, ResourceState, tuple[AdvancementEntry, ...], DevelopmentOutcome]:
    """Settle one source-authored development occurrence exactly once."""
    if not system:
        raise ValidationError("Character development requires engine authority")
    prior = _prior(resources, command)
    if prior is not None:
        return state, resources, advancement, prior
    if command.expected_revision != resources.revision:
        raise ConflictError("Character-development revision changed")
    _profile(profiles, command.actor_id)
    revision = resources.revision + 1

    if isinstance(command, SettleAdventureImprovement):
        adventure_rule = _rule(rules.adventures, command.rule_id, "adventure improvement")
        if adventure_rule.actor_id != command.actor_id:
            raise ValidationError("Adventure improvement belongs to another actor")
        if adventure_rule.id in state.settled_adventure_ids:
            raise ConflictError("Adventure improvement is already settled")
        if adventure_rule.points:
            advancement += (
                AdvancementEntry(
                    id=command.id,
                    actor_id=command.actor_id,
                    kind="earned",
                    points=adventure_rule.points,
                    revision=revision,
                    build_before=build_revision,
                    build_after=build_revision,
                    reason=adventure_rule.reason,
                    eligible_definition_ids=adventure_rule.eligible_definition_ids,
                    source_kind="adventure",
                    source_id=adventure_rule.id,
                ),
            )
        permissions = state.permissions + tuple(
            AdvancementPermission(
                id=f"{command.id}:{trait_id}",
                actor_id=command.actor_id,
                definition_id=trait_id,
                source_kind="gained-in-play",
                source_id=adventure_rule.id,
            )
            for trait_id in adventure_rule.gained_trait_ids
        )
        state = state.model_copy(
            update={
                "settled_adventure_ids": state.settled_adventure_ids + (adventure_rule.id,),
                "permissions": permissions,
            }
        )
        outcome = DevelopmentOutcome(status="awarded", points=adventure_rule.points)
    elif isinstance(command, SettleStudy):
        study_rule = _rule(rules.study, command.rule_id, "study program")
        if command.time_use_id in state.consumed_time_use_ids:
            raise ConflictError("Time Use study credit is already settled")
        entry = next(
            (item for item in administration.time_use if item.id == command.time_use_id), None
        )
        if entry is None or entry.actor_id != command.actor_id:
            raise ValidationError("Study requires the actor's settled Time Use entry")
        _instruction_valid(study_rule, profiles, command.actor_id)
        learning = _study_credit(study_rule, entry)
        current = next(
            (
                item
                for item in state.progress
                if item.actor_id == command.actor_id and item.subject_id == study_rule.subject_id
            ),
            StudyProgress(
                actor_id=command.actor_id,
                subject_id=study_rule.subject_id,
                learning_seconds=0,
                points_awarded=0,
            ),
        )
        total = current.learning_seconds + learning
        total_points = total // LEARNING_SECONDS_PER_POINT
        awarded = total_points - current.points_awarded
        progress = current.model_copy(
            update={"learning_seconds": total, "points_awarded": total_points}
        )
        all_progress = tuple(
            item
            for item in state.progress
            if (item.actor_id, item.subject_id) != (command.actor_id, study_rule.subject_id)
        ) + (progress,)
        permissions = state.permissions
        if study_rule.learnable_advantage and not any(
            item.actor_id == command.actor_id and item.definition_id == study_rule.subject_id
            for item in permissions
        ):
            permissions += (
                AdvancementPermission(
                    id=f"{command.id}:{study_rule.subject_id}",
                    actor_id=command.actor_id,
                    definition_id=study_rule.subject_id,
                    source_kind="study",
                    source_id=study_rule.id,
                ),
            )
        if awarded:
            advancement += (
                AdvancementEntry(
                    id=command.id,
                    actor_id=command.actor_id,
                    kind="earned",
                    points=awarded,
                    revision=revision,
                    build_before=build_revision,
                    build_after=build_revision,
                    reason=f"Study of {study_rule.subject_id}",
                    eligible_definition_ids=(study_rule.subject_id,),
                    source_kind="study",
                    source_id=study_rule.id,
                ),
            )
        state = state.model_copy(
            update={
                "progress": all_progress,
                "consumed_time_use_ids": state.consumed_time_use_ids + (command.time_use_id,),
                "permissions": permissions,
            }
        )
        outcome = DevelopmentOutcome(status="studied", points=awarded, learning_seconds=learning)
    else:
        quick_rule = _rule(rules.quick_learning, command.rule_id, "quick-learning opportunity")
        if quick_rule.actor_id != command.actor_id or not quick_rule.default_available:
            raise ValidationError("Quick learning requires a stressful default skill attempt")
        earned = next(
            (
                item
                for item in advancement
                if item.id == quick_rule.earned_entry_id
                and item.actor_id == command.actor_id
                and item.kind == "earned"
                and item.points > 0
            ),
            None,
        )
        if earned is None:
            raise ValidationError("Quick learning requires points from the prior session")
        actor = _profile(profiles, command.actor_id)
        levels = dict(actor.levels)
        memory_bonus = (
            10
            if "trait:photographic-memory" in actor.purchased_ids
            else 5
            if "trait:eidetic-memory" in actor.purchased_ids
            else 0
        )
        check = success_roll(
            rules.profile_id, levels.get("attribute:iq", 0) + memory_bonus, rng=rng
        )
        succeeded = check.outcome.succeeded
        attempt = QuickLearningAttempt(
            id=command.id,
            rule_id=quick_rule.id,
            actor_id=command.actor_id,
            succeeded=succeeded,
        )
        permissions = state.permissions
        if succeeded:
            permissions += (
                AdvancementPermission(
                    id=command.id,
                    actor_id=command.actor_id,
                    definition_id=quick_rule.skill_id,
                    source_kind="quick-learning",
                    source_id=quick_rule.stressful_event_id,
                ),
            )
        state = state.model_copy(
            update={
                "quick_learning": state.quick_learning + (attempt,),
                "permissions": permissions,
            }
        )
        outcome = DevelopmentOutcome(status="learned" if succeeded else "not-learned")

    resources = _finish(resources, command, outcome)
    return state, resources, advancement, outcome


def validate_development(
    rules: DevelopmentRules | None,
    state: DevelopmentState,
    administration: AdministrationState,
    advancement: tuple[AdvancementEntry, ...],
    actor_ids: frozenset[str],
) -> None:
    if rules is None:
        if state != DevelopmentState():
            raise ValidationError("Character-development state requires authored rules")
        return
    if len(set(state.consumed_time_use_ids)) != len(state.consumed_time_use_ids):
        raise ValidationError("Duplicate settled study Time Use entry")
    if not set(state.consumed_time_use_ids) <= {item.id for item in administration.time_use}:
        raise ValidationError("Study settlement references unknown Time Use")
    if len({(item.actor_id, item.subject_id) for item in state.progress}) != len(state.progress):
        raise ValidationError("Duplicate study progress")
    if any(item.actor_id not in actor_ids for item in state.progress + state.permissions):
        raise ValidationError("Character development references an unknown actor")
    if any(
        entry.source_kind != "discretionary"
        and (not entry.eligible_definition_ids or entry.source_id is None)
        for entry in advancement
        if entry.kind == "earned"
    ):
        raise ValidationError("Source-bound advancement entry lacks eligibility provenance")
