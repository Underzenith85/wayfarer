"""Stateful residual task and physical-activity procedures (B346, B350-356)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.character.traits.physiology import NO_PHYSIOLOGY_TRAITS, PhysiologyTraits
from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE = "gurps-basic-set-4e-2004"
PREFIX = "campaign-activity:"


class ActivityActor(Record):
    actor_id: Id
    basic_lift: int = Field(gt=0)
    basic_move: int = Field(gt=0)
    ht: int = Field(gt=0)
    will: int = Field(gt=0)
    encumbrance_level: int = Field(default=0, ge=0, le=4)
    current_fp: int = Field(default=10)
    maximum_fp: int = Field(default=10, gt=0)
    enhanced_move_top: int | None = Field(default=None, gt=0)
    targets: tuple[tuple[Id, int], ...] = ()
    physiology: PhysiologyTraits = NO_PHYSIOLOGY_TRAITS

    def target(self, identifier: str) -> int:
        result = next((value for key, value in self.targets if key == identifier), None)
        if result is None:
            raise ValidationError("Activity requires an authored compiled target")
        return result


class LongTaskRule(Record):
    kind: Literal["long-task"] = "long-task"
    id: Id
    target_id: Id
    required_man_hours: int = Field(gt=0)
    time_spent_modifier: int = Field(default=0, ge=-10, le=5)


class DiggingRule(Record):
    kind: Literal["digging"] = "digging"
    id: Id
    soil: Literal["loose", "ordinary", "hard-soil", "hard-rock"]
    tool: Literal["shovel", "pick", "pick-and-shovel", "improvised"]
    required_cubic_feet: Decimal = Field(gt=0)
    wooden_tools: bool = False


class BreathRule(Record):
    kind: Literal["breath"] = "breath"
    id: Id
    exertion: Literal["none", "mild", "heavy"]
    preparation: Literal["none", "deep-breath", "hyperventilate", "pure-oxygen"] = "deep-breath"
    breath_control: bool = False
    breath_holding_levels: int = Field(default=0, ge=0)


class RunningRule(Record):
    kind: Literal["running"] = "running"
    id: Id
    pace: Literal["sprint", "paced"]
    straight_and_good: bool = True


class ExtraEffortRule(Record):
    kind: Literal["extra-effort"] = "extra-effort"
    id: Id
    target_id: Id | None = None
    requested_percent: int = Field(ge=5, le=100, multiple_of=5)
    motivated: bool = False
    critical_failure_consequence: str = Field(min_length=1, max_length=300)
    task: Literal[
        "instant", "digging", "hiking", "jumping", "lifting", "running", "swimming", "throwing"
    ] = "instant"
    ordinary_fp_cost: int = Field(default=0, ge=0, le=100000)
    injury_location: Literal["torso", "left-leg", "right-leg", "left-foot", "right-foot"] = "torso"
    temporary_disadvantage: Literal["bad-back", "crippled-leg"] = "bad-back"
    running_pace: Literal["sprint", "paced"] = "sprint"

    @model_validator(mode="after")
    def source_context(self) -> ExtraEffortRule:
        if self.task == "lifting" and self.requested_percent % 10:
            raise ValueError("Lifting extra effort uses ten-percent increments")
        if self.task in {"digging", "lifting"} and (
            self.injury_location != "torso" or self.temporary_disadvantage != "bad-back"
        ):
            raise ValueError("Digging/lifting injuries require the source back consequence")
        if self.task in {"running", "jumping"} and (
            self.injury_location == "torso" or self.temporary_disadvantage != "crippled-leg"
        ):
            raise ValueError("Running/jumping injuries require an authored source leg/foot")
        return self


ActivityRule = Annotated[
    LongTaskRule | DiggingRule | BreathRule | RunningRule | ExtraEffortRule,
    Field(discriminator="kind"),
]


class PerformActivity(Command):
    kind: Literal["campaign-activity"] = "campaign-activity"
    activity_id: Id
    seconds: int = Field(gt=0)
    supervisor_target: int | None = Field(default=None, gt=0)


ACTIVITY_COMMAND_ADAPTER: TypeAdapter[PerformActivity] = TypeAdapter(PerformActivity)


class ActivityOutcome(Record):
    command_id: Id
    activity_id: Id
    procedure: Literal["long-task", "digging", "breath", "running", "extra-effort"]
    elapsed_seconds: int = Field(ge=0)
    progress: Decimal = Field(default=Decimal(0), ge=0)
    total_progress: Decimal = Field(default=Decimal(0), ge=0)
    completed: bool = False
    ruined_hours: int = Field(default=0, ge=0)
    fp_lost: int = Field(default=0, ge=0)
    move: int = Field(default=0, ge=0)
    suffocating: bool = False
    consequence: str = ""
    checks: tuple[CheckTrace, ...] = ()
    hp_lost: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)


AdvanceClock = Callable[[ResourceState, int, str], ResourceState]
LoseFatigue = Callable[[ResourceState, int, str, str], ResourceState]
EnterSuffocation = Callable[[ResourceState, str, str], ResourceState]


def gravity_effects(
    gravity: Decimal,
    *,
    body_weight: Decimal,
    gear_weight: Decimal,
    strength: int,
    basic_move: int,
    experienced: bool = False,
) -> dict[str, Decimal | int]:
    """Return B350-351 gravity projections without mutating base statistics."""
    if gravity < 0 or body_weight < 0 or gear_weight < 0 or strength < 1 or basic_move < 1:
        raise ValidationError("Invalid gravity context")
    increments = int(abs(gravity - 1) / Decimal("0.2"))
    dx_penalty = -(increments // 2 if experienced else increments)
    high_penalty = -(increments // 2) if gravity > 1 else 0
    extra_weight = (
        (body_weight + gear_weight) * (gravity - 1) if gravity > 1 else gear_weight * gravity
    )
    multiplier = Decimal(0) if gravity == 0 else Decimal(1) / gravity
    return {
        "effective_load": max(Decimal(0), extra_weight),
        "zero_g_push_move": strength // 2 if gravity == 0 else basic_move,
        "jump_multiplier": multiplier,
        "throw_multiplier": multiplier,
        "terminal_velocity_multiplier": gravity,
        "lifting_multiplier": multiplier,
        "dx_penalty": dx_penalty,
        "iq_ht_fp_penalty": high_penalty,
    }


def _history(state: ResourceState) -> tuple[ActivityOutcome, ...]:
    outcomes: list[ActivityOutcome] = []
    for event in state.events:
        if event.id.startswith(PREFIX):
            outcomes.append(ActivityOutcome.model_validate_json(event.kind))
    return tuple(outcomes)


def _digest(command: PerformActivity) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _dig_rate(rule: DiggingRule, basic_lift: int) -> tuple[Decimal, int]:
    multipliers = {
        ("loose", "shovel"): Decimal(2),
        ("ordinary", "shovel"): Decimal(1),
        ("hard-soil", "pick-and-shovel"): Decimal("0.6"),
        ("hard-rock", "pick-and-shovel"): Decimal("0.5"),
        ("hard-soil", "pick"): Decimal(2),
        ("hard-rock", "pick"): Decimal(1),
    }
    tool = "shovel" if rule.tool == "improvised" else rule.tool
    multiplier = multipliers.get((rule.soil, tool))
    if multiplier is None:
        raise ValidationError("Digging soil requires its source-defined tool sequence")
    if rule.wooden_tools:
        multiplier /= 2
    if rule.tool == "improvised":
        multiplier /= 4
    return Decimal(basic_lift) * multiplier, {
        "loose": 1,
        "ordinary": 2,
        "hard-soil": 3,
        "hard-rock": 4,
    }[rule.soil]


def _breath_limit(rule: BreathRule, ht: int) -> int:
    limit = {"none": ht * 10, "mild": ht * 4, "heavy": ht}[rule.exertion]
    if rule.preparation == "none":
        limit //= 2
    elif rule.preparation == "hyperventilate":
        limit = limit * 3 // 2
    elif rule.preparation == "pure-oxygen":
        limit = limit * 5 // 2
    if rule.breath_control:
        limit = limit * 3 // 2
    return int(limit * 2**rule.breath_holding_levels)


class _Resolution(Record):
    progress: Decimal = Decimal(0)
    fp: int = 0
    move: int = 0
    ruined: int = 0
    suffocating: bool = False
    consequence: str = ""
    checks: tuple[CheckTrace, ...] = ()
    prerequisite_checks: tuple[CheckTrace, ...] = ()
    effort_attempted: bool = True


def _long_task(
    rule: LongTaskRule,
    command: PerformActivity,
    actor: ActivityActor,
    rng: RandomSource,
) -> _Resolution:
    if command.seconds % 3600 or command.seconds > 24 * 3600:
        raise ValidationError("Long-task contributions are whole hours within one day")
    hours = command.seconds // 3600
    checks: list[CheckTrace] = []
    bonus = 0
    if command.supervisor_target is not None:
        supervision = success_roll(PROFILE, command.supervisor_target, rng=rng)
        checks.append(supervision)
        bonus = (
            2
            if supervision.outcome is Outcome.CRITICAL_SUCCESS
            else int(supervision.outcome.succeeded)
        )
    work = success_roll(
        PROFILE, actor.target(rule.target_id) + bonus + rule.time_spent_modifier, rng=rng
    )
    checks.append(work)
    progress = (
        Decimal(hours) * Decimal("1.5")
        if work.outcome is Outcome.CRITICAL_SUCCESS
        else Decimal(hours)
        if work.outcome is Outcome.SUCCESS
        else Decimal(hours) / 2
        if work.outcome is Outcome.FAILURE
        else Decimal(0)
    )
    ruined = (
        sum((rng.randbelow(6) + 1, rng.randbelow(6) + 1))
        if work.outcome is Outcome.CRITICAL_FAILURE
        else 0
    )
    fp = 0
    consequence = ""
    if hours > 8:
        overtime = success_roll(PROFILE, actor.ht - max(0, hours - 10), rng=rng)
        checks.append(overtime)
        if not overtime.outcome.succeeded:
            fp = max(2, -overtime.margin)
            consequence = (
                "exhausted-next-day" if overtime.outcome is Outcome.CRITICAL_FAILURE else ""
            )
    return _Resolution(
        progress=progress,
        fp=fp,
        ruined=ruined,
        consequence=consequence,
        checks=tuple(checks),
    )


def _digging(rule: DiggingRule, command: PerformActivity, actor: ActivityActor) -> _Resolution:
    rate, hourly_fp = _dig_rate(rule, actor.basic_lift)
    hours = Decimal(command.seconds) / Decimal(3600)
    return _Resolution(
        progress=rate * hours,
        fp=hourly_fp * ((command.seconds + 3599) // 3600),
    )


def _breath(
    rule: BreathRule,
    command: PerformActivity,
    actor: ActivityActor,
    past: tuple[ActivityOutcome, ...],
) -> _Resolution:
    purchase = actor.physiology.purchase("advantage:doesnt-breathe")
    if purchase is not None and not purchase.modifiers:
        return _Resolution(progress=Decimal(command.seconds))
    prior = sum(value.elapsed_seconds for value in past)
    limit = _breath_limit(rule, actor.ht)
    excess = max(0, prior + command.seconds - limit)
    fp = excess - max(0, prior - limit)
    return _Resolution(
        progress=Decimal(command.seconds),
        fp=fp,
        suffocating=excess > 0,
        consequence="canonical-suffocation" if excess > 0 else "",
    )


def _running(
    rule: RunningRule,
    command: PerformActivity,
    actor: ActivityActor,
    past: tuple[ActivityOutcome, ...],
    rng: RandomSource,
) -> _Resolution:
    base_move = max(1, actor.basic_move * (10 - 2 * actor.encumbrance_level) // 10)
    if actor.current_fp * 3 < actor.maximum_fp:
        base_move = max(1, (base_move + 1) // 2)
    sprint = max(base_move + 1, base_move * 6 // 5)
    previous_seconds = sum(value.elapsed_seconds for value in past)
    distances: list[int] = []
    for offset in range(command.seconds):
        second = previous_seconds + offset
        speed = base_move if second == 0 or not rule.straight_and_good else sprint
        if actor.enhanced_move_top is not None and second > 0 and rule.straight_and_good:
            speed = min(actor.enhanced_move_top, base_move * (second + 1))
        distances.append(speed if rule.pace == "sprint" else max(1, speed // 2))
    interval = 15 if rule.pace == "sprint" else 60
    due = (previous_seconds + command.seconds) // interval - previous_seconds // interval
    checks = tuple(
        success_roll(PROFILE, max(actor.ht, actor.target("skill:running")), rng=rng)
        for _ in range(due)
    )
    return _Resolution(
        progress=Decimal(sum(distances)),
        fp=sum(not check.outcome.succeeded for check in checks),
        move=distances[-1],
        checks=checks,
    )


def _extra_effort(rule: ExtraEffortRule, actor: ActivityActor, rng: RandomSource) -> _Resolution:
    target = actor.will if rule.target_id is None else actor.target(rule.target_id)
    target += 5 if rule.motivated else 0
    target -= rule.requested_percent // 5
    target -= max(0, actor.maximum_fp - actor.current_fp)
    if rule.task == "lifting":
        target += rule.requested_percent // 5 - rule.requested_percent // 10
    check = success_roll(PROFILE, target, rng=rng)
    return _Resolution(
        progress=Decimal(rule.requested_percent if check.outcome.succeeded else 0),
        fp=rule.ordinary_fp_cost
        + int(check.outcome is not Outcome.CRITICAL_SUCCESS) * (2 if rule.task == "hiking" else 1),
        consequence=(
            rule.critical_failure_consequence if check.outcome is Outcome.CRITICAL_FAILURE else ""
        ),
        checks=(check,),
    )


def _resolve(
    rule: ActivityRule,
    command: PerformActivity,
    actor: ActivityActor,
    past: tuple[ActivityOutcome, ...],
    rng: RandomSource,
) -> _Resolution:
    if isinstance(rule, LongTaskRule):
        return _long_task(rule, command, actor, rng)
    if isinstance(rule, DiggingRule):
        return _digging(rule, command, actor)
    if isinstance(rule, BreathRule):
        return _breath(rule, command, actor, past)
    if isinstance(rule, RunningRule):
        return _running(rule, command, actor, past, rng)
    return _extra_effort(rule, actor, rng)


def _effort_pools(state: ResourceState, actor: ActivityActor) -> None:
    hp = next((p for p in state.pools if p.id == "hp:" + actor.actor_id), None)
    fp = next((p for p in state.pools if p.id == "fp:" + actor.actor_id), None)
    if hp is None or hp.injury is None or fp is None or fp.fatigue is None:
        raise ValidationError("Extra effort requires canonical HP and FP pools")
    if hp.injury.incapacitated or hp.injury.machine:
        raise ValidationError("Incapacitated actors and Machines cannot use extra effort")
    if hp.injury.anatomy != "human":
        raise ValidationError("Extra-effort lasting consequences require explicit human anatomy")
    if (fp.current, fp.maximum) != (actor.current_fp, actor.maximum_fp):
        raise ValidationError("Extra effort requires current canonical fatigue")


def _effort_interval(rule: ExtraEffortRule, command: PerformActivity) -> None:
    interval = {
        "instant": 1,
        "jumping": 1,
        "throwing": 1,
        "digging": 3600,
        "hiking": 86400,
        "lifting": 60,
        "swimming": 60,
        "running": 15 if rule.running_pace == "sprint" else 60,
    }[rule.task]
    if command.seconds != interval:
        raise ValidationError("Extra effort requires one source-defined task interval per roll")


def _effort_consequences(
    state: ResourceState,
    command: PerformActivity,
    rule: ExtraEffortRule,
    actor: ActivityActor,
    result: _Resolution,
    rng: RandomSource,
) -> tuple[ResourceState, int, tuple[CheckTrace, ...], int]:
    if not result.effort_attempted:
        return state, 0, result.checks, result.fp
    state, fatigue = apply_fatigue(
        state,
        FatigueCost(
            id=command.id + ":effort-fp",
            actor_id=actor.actor_id,
            expected_revision=state.revision,
            amount=result.fp,
        ),
        ht=actor.ht,
        rng=rng,
        system=True,
    )
    checks = result.prerequisite_checks + result.checks + fatigue.checks
    hp_lost = fatigue.hp_lost
    if result.checks[0].outcome is not Outcome.CRITICAL_FAILURE:
        return state, hp_lost, checks, fatigue.fp_lost
    state, injury = apply_injury(
        state,
        Wound(
            id=command.id + ":effort-injury",
            actor_id=actor.actor_id,
            expected_revision=state.revision,
            basic_damage=fatigue.fp_lost
            + (max(0, actor.maximum_fp - actor.current_fp) if rule.task == "hiking" else 0),
            resistance=0,
            damage_type="cr",
            location=None,
            injury_source="internal",
        ),
        ht=actor.ht,
        rng=rng,
        system=True,
        exertion_location=rule.injury_location,
    )
    hp_lost += injury.injury
    checks += tuple(c.check for c in injury.checks)
    hp = next(p for p in state.pools if p.id == "hp:" + actor.actor_id)
    assert hp.injury is not None
    status = hp.injury
    if rule.task in {"digging", "lifting"}:
        status = status.model_copy(
            update={"rest_only_injury": status.rest_only_injury + injury.injury}
        )
    if result.checks[0].total == 18:
        secondary = success_roll(PROFILE, actor.ht, rng=rng)
        checks += (secondary,)
        if not secondary.outcome.succeeded:
            permanent = secondary.outcome is Outcome.CRITICAL_FAILURE
            deadline = None if permanent else state.game_time + (rng.randbelow(6) + 1) * 30 * 86400
            effect = LastingInjury(
                id=command.id + ":effort-disadvantage",
                location=rule.injury_location,
                kind="bad-back" if rule.temporary_disadvantage == "bad-back" else "crippled",
                duration="permanent" if permanent else "lasting",
                inflicted_at=state.game_time,
                injury=injury.injury,
                recovery_at=deadline,
            )
            status = status.model_copy(
                update={"lasting_injuries": status.lasting_injuries + (effect,)}
            )
    hp = hp.model_copy(update={"injury": status})
    return (
        state.model_copy(update={"pools": tuple(hp if p.id == hp.id else p for p in state.pools)}),
        hp_lost,
        checks,
        fatigue.fp_lost,
    )


def _resolve_activity(
    state: ResourceState,
    rule: ActivityRule,
    command: PerformActivity,
    actor: ActivityActor,
    past: tuple[ActivityOutcome, ...],
    rng: RandomSource,
) -> tuple[ResourceState, _Resolution]:
    if not isinstance(rule, ExtraEffortRule):
        return state, _resolve(rule, command, actor, past, rng)
    state, permission = apply_fatigue(
        state,
        ContinueExertion(
            id=command.id + ":exertion", actor_id=actor.actor_id, expected_revision=state.revision
        ),
        ht=actor.ht,
        will=actor.will,
        rng=rng,
        system=True,
    )
    if not permission.allowed:
        return state, _Resolution(
            fp=permission.fp_lost,
            checks=permission.checks,
            effort_attempted=False,
            consequence="exertion-unavailable",
        )
    result = _resolve(rule, command, actor, past, rng)
    return state, result.model_copy(update={"prerequisite_checks": permission.checks})


def apply_activity(
    state: ResourceState,
    command: PerformActivity,
    rule: ActivityRule,
    actor: ActivityActor,
    *,
    rng: RandomSource,
    advance: AdvanceClock,
    lose_fatigue: LoseFatigue,
    enter_suffocation: EnterSuffocation | None = None,
    system: bool = False,
) -> tuple[ResourceState, ActivityOutcome]:
    """Resolve one interval with CAS, replay receipts and the shared campaign clock."""
    if not system:
        raise ValidationError("Campaign activities require engine authority")
    prior = next((value for value in state.receipts if value.command_id == command.id), None)
    if prior is not None:
        if prior.digest != _digest(command):
            raise ConflictError("Activity command ID reused")
        event = next(value for value in state.events if value.id == PREFIX + command.id)
        return state, ActivityOutcome.model_validate_json(event.kind)
    if command.expected_revision != state.revision:
        raise ConflictError("Activity revision changed")
    if actor.actor_id != command.actor_id or rule.id != command.activity_id:
        raise ValidationError("Activity is not bound to this actor and command")
    if isinstance(rule, ExtraEffortRule):
        _effort_pools(state, actor)
        _effort_interval(rule, command)

    past = tuple(value for value in _history(state) if value.activity_id == rule.id)
    total = sum((value.progress for value in past), Decimal(0))
    state, result = _resolve_activity(state, rule, command, actor, past, rng)

    hp_lost, checks, fp_lost = 0, result.checks, result.fp
    if isinstance(rule, ExtraEffortRule):
        state, hp_lost, checks, fp_lost = _effort_consequences(
            state, command, rule, actor, result, rng
        )
    elif result.fp:
        if result.suffocating and actor.current_fp - result.fp <= 0 and enter_suffocation is None:
            raise ValidationError("Breath exhaustion requires the canonical suffocation reducer")
        state = lose_fatigue(state, result.fp, command.id, command.actor_id)
        if result.suffocating and actor.current_fp - result.fp <= 0:
            assert enter_suffocation is not None
            state = enter_suffocation(state, command.id, command.actor_id)
    if result.effort_attempted:
        state = advance(state, state.game_time + command.seconds, command.id)
    total = max(Decimal(0), total - result.ruined) + result.progress
    required = (
        Decimal(rule.required_man_hours)
        if isinstance(rule, LongTaskRule)
        else rule.required_cubic_feet
        if isinstance(rule, DiggingRule)
        else Decimal(0)
    )
    outcome = ActivityOutcome(
        command_id=command.id,
        activity_id=rule.id,
        procedure=rule.kind,
        elapsed_seconds=command.seconds if result.effort_attempted else 0,
        progress=result.progress,
        total_progress=total,
        completed=required > 0 and total >= required,
        ruined_hours=result.ruined,
        fp_lost=fp_lost,
        move=result.move,
        suffocating=result.suffocating,
        consequence=result.consequence,
        checks=checks,
        hp_lost=hp_lost,
    )
    state = state.model_copy(
        update={
            "revision": state.revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": state.events
            + (
                ResourceEvent(
                    id=PREFIX + command.id,
                    at=state.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )
    return ResourceState.model_validate(state), outcome
