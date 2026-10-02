"""Private B346 task phases for a host that persists each unresolved original.

The host advances the campaign clock before preparing a shift. It binds current
approved actors and equipment, then exposes only one phase's original at a time.
Selecting a prerequisite commits its consequences before another roll can open.
Selecting the worker commits progress, and only then draws critical-failure 2d.
This module never advances time or rolls a replacement for a completed procedure.

Protect PRIVATE_PREFIXES at genesis and in player projections. These records are
engine-owned evidence, not task definitions or caller-supplied numeric authority.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import (
    CheckTrace,
    Modifier,
    Outcome,
    RandomSource,
    evaluate_success,
)
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.campaign.activities import (
    PREFIX as ACTIVITY_PREFIX,
)
from wayfarer.engine.simulation.campaign.activities import (
    PROFILE,
    ActivityActor,
    ActivityOutcome,
    LongTaskRule,
    PerformActivity,
    _long_task_result,
)
from wayfarer.engine.simulation.health.fatigue import FatigueCost, FatigueResult, apply_fatigue
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PHASE_PREFIX = "campaign-long-task-v2:"
REST_PREFIX = "campaign-long-task-rest-v2:"
PRIVATE_PREFIXES = (PHASE_PREFIX, REST_PREFIX)
DAY = 86400
Phase = Literal["supervisor-overtime", "supervision", "overtime", "worker", "complete"]


class LongTaskPreparation(Record):
    """A captured trusted shift, with exactly one unresolved success roll."""

    version: Literal[2] = 2
    command: PerformActivity
    rule: LongTaskRule
    actor: ActivityActor
    started_at: int = Field(ge=0)
    resource_revision: int = Field(ge=0)
    supervisor: ActivityActor | None = None
    supervisor_target_id: Literal["skill:administration", "skill:leadership"] | None = None
    worker_modifiers: tuple[Modifier, ...] = ()
    supervisor_modifiers: tuple[Modifier, ...] = ()
    worker_ht_modifiers: tuple[Modifier, ...] = ()
    supervisor_ht_modifiers: tuple[Modifier, ...] = ()
    phase: Phase
    supervisor_overtime: CheckTrace | None = None
    supervisor_fatigue: FatigueResult | None = None
    supervision: CheckTrace | None = None
    overtime: CheckTrace | None = None
    fatigue: FatigueResult | None = None
    worker: CheckTrace | None = None

    @model_validator(mode="after")
    def coherent_shift(self) -> LongTaskPreparation:
        if self.command.actor_id != self.actor.actor_id or self.command.activity_id != self.rule.id:
            raise ValueError("Long task is not bound to this worker and rule")
        if self.command.supervisor_target is not None:
            raise ValueError("Long-task supervision requires a separate compiled actor")
        if self.command.seconds % 3600 or self.command.seconds > DAY:
            raise ValueError("Long-task contributions are whole hours within one day")
        if (self.supervisor is None) != (self.supervisor_target_id is None):
            raise ValueError("Supervision requires an actor and an authored supervision skill")
        if self.supervisor is not None and self.supervisor.actor_id == self.actor.actor_id:
            raise ValueError("A supervisor coordinates a separate worker instead of working")
        if (self.supervision is not None) != (
            self.supervisor is not None and self.phase in {"overtime", "worker", "complete"}
        ):
            raise ValueError("Supervision must be selected before the next phase")
        if (self.supervisor_overtime is not None) != (
            self.supervisor is not None and self.hours > 8 and self.phase != "supervisor-overtime"
        ):
            raise ValueError("Supervisor overtime must be selected before supervision")
        if (self.overtime is not None) != (self.hours > 8 and self.phase in {"worker", "complete"}):
            raise ValueError("Overtime must be selected before the worker check")
        if self.phase in {"supervisor-overtime", "supervision"} and self.supervisor is None:
            raise ValueError("No supervisor check is due")
        if self.phase == "supervisor-overtime" and self.hours <= 8:
            raise ValueError("No supervisor overtime check is due")
        if self.phase == "overtime" and self.hours <= 8:
            raise ValueError("No overtime check is due")
        if (self.worker is not None) != (self.phase == "complete"):
            raise ValueError("Only a complete shift has a selected worker check")
        if (self.fatigue is not None) != (
            self.overtime is not None and not self.overtime.outcome.succeeded
        ):
            raise ValueError("Failed overtime requires its committed fatigue result")
        if (self.supervisor_fatigue is not None) != (
            self.supervisor_overtime is not None and not self.supervisor_overtime.outcome.succeeded
        ):
            raise ValueError("Failed supervisor overtime requires its committed fatigue result")
        return self

    @property
    def hours(self) -> int:
        return self.command.seconds // 3600

    @property
    def completed_at(self) -> int:
        return self.started_at + self.command.seconds

    @property
    def check_actor_id(self) -> str:
        if self.phase in {"supervisor-overtime", "supervision"}:
            assert self.supervisor is not None
            return self.supervisor.actor_id
        return self.actor.actor_id

    @property
    def check_id(self) -> str:
        """Stable private roll identity, including a different identity per phase."""
        return (
            PHASE_PREFIX + hashlib.sha256(self.command.id.encode()).hexdigest() + ":" + self.phase
        )

    @property
    def check_target(self) -> int:
        base, modifiers = _check_context(self)
        return base + sum(modifier.value for modifier in modifiers)


class LongTaskWorkRestriction(Record):
    command_id: Id
    actor_id: Id
    starts_at: int = Field(ge=0)
    ends_at: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> LongTaskWorkRestriction:
        if self.ends_at - self.starts_at != DAY:
            raise ValueError("Long-task exhaustion forbids the next full work day")
        return self


class _Selection(Record):
    preparation: LongTaskPreparation
    check: CheckTrace


class LongTaskTransition(Record):
    """Persisted selected phase and its result; allows exact retry without dice."""

    selection: _Selection
    next_preparation: LongTaskPreparation
    outcome: ActivityOutcome | None = None


def ensure_long_task_available(
    state: ResourceState, actor_id: str, *, started_at: int, seconds: int
) -> None:
    """Reject another contribution within a worker's daily shift or mandatory rest.

    A work day is a 24-hour interval anchored at the shift's start, including for
    a shift across midnight. Exhaustion forbids the following 24-hour interval.
    The explicit interval is persisted at HT selection, before worker selection.
    A contribution is a full daily shift: splitting one day across commands or
    projects is unsupported, and cannot bypass overtime or supervisor commitment.
    """
    if started_at < 0 or seconds < 3600 or seconds % 3600 or seconds > DAY:
        raise ValidationError("Long-task contributions are whole hours within one day")
    ends_at = started_at + seconds
    for event in state.events:
        if event.id.startswith(REST_PREFIX):
            rest = LongTaskWorkRestriction.model_validate_json(event.kind)
            if rest.actor_id == actor_id and started_at < rest.ends_at and ends_at > rest.starts_at:
                raise ValidationError("Worker cannot work during exhausted next day")
        elif event.id.startswith(PHASE_PREFIX):
            prior = LongTaskTransition.model_validate_json(event.kind).next_preparation
            participants = {prior.actor.actor_id}
            if prior.supervisor is not None:
                participants.add(prior.supervisor.actor_id)
            if (
                actor_id in participants
                and started_at < prior.started_at + DAY
                and ends_at > prior.started_at
            ):
                raise ValidationError("Long-task worker or supervisor already worked this day")


def prepare_long_task(
    state: ResourceState,
    command: PerformActivity,
    rule: LongTaskRule,
    actor: ActivityActor,
    *,
    started_at: int,
    supervisor: ActivityActor | None = None,
    supervisor_target_id: Literal["skill:administration", "skill:leadership"] | None = None,
    worker_modifiers: tuple[Modifier, ...] = (),
    supervisor_modifiers: tuple[Modifier, ...] = (),
    worker_ht_modifiers: tuple[Modifier, ...] = (),
    supervisor_ht_modifiers: tuple[Modifier, ...] = (),
) -> LongTaskPreparation:
    """Capture a shift after clock advancement, without rolling or applying effects."""
    if state.game_time != started_at + command.seconds:
        raise ValidationError("Settle long-task elapsed time before opening its checks")
    ensure_long_task_available(
        state, actor.actor_id, started_at=started_at, seconds=command.seconds
    )
    actor.target(rule.target_id)
    if supervisor is not None:
        ensure_long_task_available(
            state, supervisor.actor_id, started_at=started_at, seconds=command.seconds
        )
        if supervisor_target_id is not None:
            supervisor.target(supervisor_target_id)
    phase: Phase = (
        ("supervisor-overtime" if command.seconds > 8 * 3600 else "supervision")
        if supervisor is not None
        else "overtime"
        if command.seconds > 8 * 3600
        else "worker"
    )
    return LongTaskPreparation(
        command=command,
        rule=rule,
        actor=actor,
        started_at=started_at,
        resource_revision=state.revision,
        supervisor=supervisor,
        supervisor_target_id=supervisor_target_id,
        worker_modifiers=worker_modifiers,
        supervisor_modifiers=supervisor_modifiers,
        worker_ht_modifiers=worker_ht_modifiers,
        supervisor_ht_modifiers=supervisor_ht_modifiers,
        phase=phase,
    )


def _modifier(value: int, reason: str) -> Modifier:
    return Modifier(value, reason, "sjg:basic-set-campaigns-4e-2004:b346", BASELINE_ID)


def _check_context(preparation: LongTaskPreparation) -> tuple[int, tuple[Modifier, ...]]:
    if preparation.phase == "complete":
        raise ValidationError("Long task has no unresolved check")
    if preparation.phase == "supervision":
        assert preparation.supervisor is not None and preparation.supervisor_target_id is not None
        overtime = preparation.supervisor_overtime
        supervision_modifiers = preparation.supervisor_modifiers + (
            (_modifier(min(-2, overtime.margin), "Failed supervisor overtime HT"),)
            if overtime is not None and not overtime.outcome.succeeded
            else ()
        )
        return preparation.supervisor.target(
            preparation.supervisor_target_id
        ), supervision_modifiers
    if preparation.phase in {"supervisor-overtime", "overtime"}:
        actor = (
            preparation.supervisor
            if preparation.phase == "supervisor-overtime"
            else preparation.actor
        )
        assert actor is not None
        ht_modifiers = (
            preparation.supervisor_ht_modifiers
            if preparation.phase == "supervisor-overtime"
            else preparation.worker_ht_modifiers
        )
        penalty = -max(0, preparation.hours - 10)
        return actor.ht, ht_modifiers + (
            (_modifier(penalty, "Overtime shift length"),) if penalty else ()
        )
    modifiers: list[Modifier] = list(preparation.worker_modifiers)
    if preparation.supervision is not None and preparation.supervision.outcome.succeeded:
        bonus = 2 if preparation.supervision.outcome is Outcome.CRITICAL_SUCCESS else 1
        modifiers.append(_modifier(bonus, "Separate supervisor check"))
    if preparation.rule.time_spent_modifier:
        modifiers.append(
            _modifier(preparation.rule.time_spent_modifier, "Authored task time spent")
        )
    if preparation.overtime is not None and not preparation.overtime.outcome.succeeded:
        modifiers.append(_modifier(min(-2, preparation.overtime.margin), "Failed overtime HT"))
    return preparation.actor.target(preparation.rule.target_id), tuple(modifiers)


def roll_long_task_check(preparation: LongTaskPreparation, *, rng: RandomSource) -> CheckTrace:
    """Draw only the current original; the host persists it before any next roll."""
    preparation = LongTaskPreparation.model_validate(preparation)
    target, modifiers = _check_context(preparation)
    return success_roll(PROFILE, target, modifiers, rng=rng)


def score_long_task_check(
    preparation: LongTaskPreparation, dice: tuple[int, int, int]
) -> CheckTrace:
    """Score the server-selected faces without consuming entropy or rerunning a phase."""
    preparation = LongTaskPreparation.model_validate(preparation)
    target, modifiers = _check_context(preparation)
    return evaluate_success(
        target,
        modifiers,
        dice,
        rules_package=PROFILE,
        rules_version=BASELINE_ID,
        rule_id="gurps.check.success",
    )


def _total_progress(state: ResourceState, rule: LongTaskRule) -> Decimal:
    total = Decimal(0)
    for event in state.events:
        if event.id.startswith(ACTIVITY_PREFIX):
            outcome = ActivityOutcome.model_validate_json(event.kind)
            if outcome.activity_id == rule.id:
                total = max(Decimal(0), total - outcome.ruined_hours) + outcome.progress
    return total


def _fatigue_checks(fatigue: FatigueResult | None) -> tuple[CheckTrace, ...]:
    if fatigue is None:
        return ()
    return fatigue.checks + (
        tuple(value.check for value in fatigue.injury.checks) if fatigue.injury is not None else ()
    )


def select_long_task_check(
    state: ResourceState,
    preparation: LongTaskPreparation,
    check: CheckTrace,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, LongTaskPreparation, ActivityOutcome | None]:
    """Commit one selected phase; never draw the next phase's original.

    For overtime, real FP/HP effects and rest restrictions commit here. For the
    worker, only progress and a selected critical failure's 2d follow-up remain.
    The host atomically persists this state together with any Luck expenditure.
    """
    if not system:
        raise ValidationError("Long-task phases require engine authority")
    preparation = LongTaskPreparation.model_validate(preparation)
    if check != score_long_task_check(preparation, check.dice):
        raise ValidationError("Selected long-task check does not match its captured phase")
    selection = _Selection(preparation=preparation, check=check)
    digest = hashlib.sha256(selection.model_dump_json().encode()).hexdigest()
    identity = preparation.check_id
    prior = next((receipt for receipt in state.receipts if receipt.command_id == identity), None)
    if prior is not None:
        if prior.digest != digest:
            raise ConflictError("Long-task phase already selected a different check")
        event = next(event for event in state.events if event.id == identity)
        transition = LongTaskTransition.model_validate_json(event.kind)
        return state, transition.next_preparation, transition.outcome
    if (
        state.revision != preparation.resource_revision
        or state.game_time != preparation.completed_at
    ):
        raise ConflictError("Long-task resource state changed after its check was prepared")
    updates: dict[str, object] = {}
    outcome: ActivityOutcome | None = None
    if preparation.phase == "supervision":
        updates.update(supervision=check, phase="overtime" if preparation.hours > 8 else "worker")
    elif preparation.phase in {"supervisor-overtime", "overtime"}:
        supervisor_overtime = preparation.phase == "supervisor-overtime"
        actor = preparation.supervisor if supervisor_overtime else preparation.actor
        assert actor is not None
        updates["supervisor_overtime" if supervisor_overtime else "overtime"] = check
        updates["phase"] = "supervision" if supervisor_overtime else "worker"
        if not check.outcome.succeeded:
            state, fatigue_result = apply_fatigue(
                state,
                FatigueCost(
                    id=identity + ":fatigue",
                    actor_id=actor.actor_id,
                    expected_revision=state.revision,
                    amount=max(2, -check.margin),
                ),
                ht=actor.ht,
                rng=rng,
                system=True,
            )
            updates["supervisor_fatigue" if supervisor_overtime else "fatigue"] = fatigue_result
        if check.outcome is Outcome.CRITICAL_FAILURE:
            rest = LongTaskWorkRestriction(
                command_id=preparation.command.id,
                actor_id=actor.actor_id,
                starts_at=preparation.started_at + DAY,
                ends_at=preparation.started_at + 2 * DAY,
            )
            state = state.model_copy(
                update={
                    "events": state.events
                    + (
                        ResourceEvent(
                            id=REST_PREFIX + hashlib.sha256(identity.encode()).hexdigest(),
                            at=state.game_time,
                            target_id=actor.actor_id,
                            kind=rest.model_dump_json(),
                        ),
                    )
                }
            )
    elif preparation.phase == "worker":
        # B346 extends the time-worked basis after successful HT. After failed
        # HT it retains extra labor for skill success; use the ordinary-day
        # failure baseline when neither check succeeded.
        contribution_hours = (
            8
            if preparation.overtime is not None
            and not preparation.overtime.outcome.succeeded
            and check.outcome is Outcome.FAILURE
            else preparation.hours
        )
        result = _long_task_result(contribution_hours, check, rng)
        total = (
            max(Decimal(0), _total_progress(state, preparation.rule) - result.ruined)
            + result.progress
        )
        checks: tuple[CheckTrace, ...] = (
            (preparation.supervisor_overtime,)
            if preparation.supervisor_overtime is not None
            else ()
        )
        checks += _fatigue_checks(preparation.supervisor_fatigue)
        checks += tuple(
            value for value in (preparation.supervision, preparation.overtime) if value is not None
        )
        fatigue = preparation.fatigue
        checks += _fatigue_checks(fatigue)
        outcome = ActivityOutcome(
            command_id=preparation.command.id,
            activity_id=preparation.rule.id,
            procedure="long-task",
            elapsed_seconds=preparation.command.seconds,
            progress=result.progress,
            total_progress=total,
            completed=total >= preparation.rule.required_man_hours,
            ruined_hours=result.ruined,
            fp_lost=fatigue.fp_lost if fatigue is not None else 0,
            hp_lost=fatigue.hp_lost if fatigue is not None else 0,
            consequence="exhausted-next-day"
            if preparation.overtime is not None
            and preparation.overtime.outcome is Outcome.CRITICAL_FAILURE
            else "",
            checks=checks + (check,),
        )
        state = state.model_copy(
            update={
                "events": state.events
                + (
                    ResourceEvent(
                        id=ACTIVITY_PREFIX + preparation.command.id,
                        at=state.game_time,
                        target_id=preparation.actor.actor_id,
                        kind=outcome.model_dump_json(),
                    ),
                )
            }
        )
        updates.update(worker=check, phase="complete")
    else:
        raise ValidationError("Long task has already completed")
    updates["resource_revision"] = state.revision + 1
    following = LongTaskPreparation.model_validate(preparation.model_copy(update=updates))
    transition = LongTaskTransition(
        selection=selection, next_preparation=following, outcome=outcome
    )
    state = state.model_copy(
        update={
            "revision": state.revision + 1,
            "receipts": state.receipts + (Receipt(command_id=identity, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=identity,
                    at=state.game_time,
                    target_id=preparation.check_actor_id,
                    kind=transition.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(state), following, outcome
