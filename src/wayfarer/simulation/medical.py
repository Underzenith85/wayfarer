"""Timed recovery and treatment with durable, per-wound attempt limits.

Numeric source: Basic Set Campaigns fourth printing, B424-427. Tasks use
ResourceState.game_time in seconds and never move the shared clock themselves.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.recovery_types import ProfileId, RecoveryTask
from wayfarer.simulation.injury import InjuryResult, Wound, apply_injury
from wayfarer.simulation.resources import Command, Receipt, Record, ResourceEvent, ResourceState


class BeginRecovery(Command):
    kind: Literal["rest", "natural", "bandage", "first-aid", "physician"]
    target_id: str
    wound_id: str | None = None
    seconds: int = Field(default=600, ge=1, le=604800)


class FinishRecovery(Command):
    kind: Literal["finish-recovery"] = "finish-recovery"
    task_id: str


@dataclass(frozen=True)
class CareContext:
    profile_id: ProfileId
    ht: int
    skill: int | None = None
    technology_level: int = 8
    food: bool = False
    water: bool = False
    sleep: bool = False
    physician_skill: int | None = None
    physician_id: str | None = None


class RecoveryResult(Record):
    task_id: str
    status: Literal["pending", "completed", "interrupted"]
    hp_recovered: int = 0
    fp_recovered: int = 0
    check: CheckTrace | None = None
    healing_die: int | None = None


def first_aid_parameters(tl: int) -> tuple[int, int]:
    """Seconds and modifier to 1d; high-HP multiplier is applied separately."""
    if not 0 <= tl <= 12:
        raise ValidationError("Unsupported medical technology level")
    return (
        1800 if tl < 5 else 1200 if tl < 8 else 600,
        -4 if tl < 2 else -3 if tl < 4 else -2 if tl < 6 else -1 if tl < 8 else 0 if tl == 8 else 1,
    )


def physician_parameters(tl: int) -> tuple[int, int]:
    if not 1 <= tl <= 12:
        raise ValidationError("Physician treatment requires TL1 or later")
    days, patients = {
        1: (7, 10),
        2: (7, 10),
        3: (7, 10),
        4: (3, 10),
        5: (2, 15),
        6: (1, 20),
        7: (1, 25),
        8: (1, 50),
        9: (1, 50),
        10: (1, 50),
        11: (1, 100),
        12: (1, 200),
    }[tl]
    return days * 86400 // (tl - 7 if tl >= 9 else 1), patients


def _wound(state: ResourceState, wound_id: str | None, target: str) -> InjuryResult:
    event = next(
        (
            e
            for e in state.events
            if e.id == wound_id and e.target_id == target and e.id.startswith("injury:")
        ),
        None,
    )
    if event is None:
        raise ValidationError("First aid requires the patient's recorded wound")
    result = InjuryResult.model_validate_json(event.kind)
    if result.injury <= 0:
        raise ValidationError("First aid requires an actual injury")
    return result


def apply_recovery(
    state: ResourceState,
    command: BeginRecovery | FinishRecovery,
    context: CareContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, RecoveryResult]:
    if not system:
        raise ValidationError("Recovery requires authoritative treatment context")
    ResourceState.model_validate(state)
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Recovery command ID reused")
        event = next(e for e in state.events if e.id == f"care:{command.id}")
        return state, RecoveryResult.model_validate_json(event.kind)
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    if type(context.ht) is not int or context.ht < 1:
        raise ValidationError("Recovery requires compiled HT")
    task = next(
        (
            t
            for t in state.recovery_tasks
            if isinstance(command, FinishRecovery) and t.id == command.task_id
        ),
        None,
    )
    if isinstance(command, FinishRecovery) and (task is None or task.actor_id != command.actor_id):
        raise ValidationError("Unknown or unauthorized recovery task")
    target = (
        command.target_id if isinstance(command, BeginRecovery) else task.target_id if task else ""
    )
    hp = next((p for p in state.pools if p.id == f"hp:{target}"), None)
    fp = next((p for p in state.pools if p.id == f"fp:{target}"), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != context.profile_id:
        raise ValidationError("Recovery requires matching profile HP")
    if hp.injury.dead or hp.injury.mortal_wound:
        raise ValidationError("Dead or mortally wounded patients require separate treatment")
    if fp is not None and fp.fatigue is not None and fp.fatigue.heart_attack:
        raise ValidationError("Heart attack requires resuscitation, not ordinary recovery")
    check = None
    die = None
    healed = restored = 0
    if isinstance(command, BeginRecovery):
        if any(
            t.status == "pending"
            and ({t.actor_id, t.target_id} & {command.actor_id, target})
            and not (
                t.kind == command.kind == "physician"
                and t.actor_id == command.actor_id
                and t.target_id != target
            )
            and not (
                t.target_id == target
                and {t.kind, command.kind} in ({"rest", "natural"}, {"natural", "physician"})
            )
            for t in state.recovery_tasks
        ):
            raise ConflictError("Actor or patient already has pending recovery")
        duration = command.seconds
        bandaged = 0
        if command.kind in ("bandage", "first-aid"):
            _wound(state, command.wound_id, target)
            previous = [
                t
                for t in state.recovery_tasks
                if t.target_id == target and t.wound_id == command.wound_id
            ]
            if any(t.kind == command.kind and t.status == "completed" for t in previous):
                raise ConflictError("This treatment has already been attempted for the wound")
            if command.kind == "bandage" and any(
                t.kind == "first-aid" and t.status == "completed" for t in previous
            ):
                raise ConflictError("First aid already includes bandaging")
            bandaged = max((t.bandaged_hp for t in previous if t.status == "completed"), default=0)
            duration = (
                60
                if command.kind == "bandage"
                else first_aid_parameters(context.technology_level)[0]
            )
        elif command.kind == "natural":
            if command.actor_id != target or not context.food:
                raise ValidationError("Natural recovery needs a day of rest and decent food")
            duration = 86400
        elif command.kind == "physician":
            duration, maximum = physician_parameters(context.technology_level)
            if (
                sum(
                    t.kind == "physician"
                    and t.actor_id == command.actor_id
                    and t.status == "pending"
                    for t in state.recovery_tasks
                )
                >= maximum
            ):
                raise ValidationError("Physician patient capacity exceeded")
        if command.kind in ("first-aid", "physician") and (
            context.skill is None or context.skill < 1
        ):
            raise ValidationError("Treatment requires a compiled medical skill")
        if command.kind == "rest" and (
            command.actor_id != target
            or fp is None
            or fp.fatigue is None
            or fp.fatigue.profile_id != context.profile_id
        ):
            raise ValidationError("Rest requires the actor's profile FP pool")
        task = RecoveryTask(
            id=command.id,
            actor_id=command.actor_id,
            target_id=target,
            profile_id=context.profile_id,
            kind=command.kind,
            start=state.game_time,
            due=state.game_time + duration,
            wound_id=command.wound_id,
            bandaged_hp=bandaged,
            technology_level=context.technology_level,
            food=context.food,
            water=context.water,
            sleep=context.sleep,
            physician_skill=context.physician_skill,
            physician_id=context.physician_id,
        )
        tasks = state.recovery_tasks + (task,)
        result = RecoveryResult(task_id=task.id, status="pending")
    else:
        assert task is not None
        if task.settled or task.status == "completed":
            raise ConflictError("Recovery task is no longer pending")
        if task.profile_id != context.profile_id or (
            task.status != "interrupted" and state.game_time < task.due
        ):
            raise ValidationError("Recovery is not due in this profile")
        multiplier = max(1, hp.maximum // 10)
        if task.status == "interrupted" and task.kind != "rest":
            pass
        elif task.kind == "rest":
            if fp is None or fp.fatigue is None:
                raise ValidationError("Rest requires profile FP")
            status = fp.fatigue
            seconds = (
                min(task.due, task.interrupted_at if task.interrupted_at is not None else task.due)
                - task.start
            )
            restricted = status.starvation + status.dehydration + status.sleep
            ordinary = min(seconds // 600, fp.maximum - fp.current - restricted)
            starvation = min(status.starvation, 3 * (seconds // 86400)) if task.food else 0
            dehydration = status.dehydration if task.water and seconds >= 86400 else 0
            sleep = (
                min(status.sleep, 1 + (seconds - 28800) // 3600)
                if task.sleep and seconds >= 28800
                else 0
            )
            restored = ordinary + starvation + dehydration + sleep
            current = fp.current + restored
            status = status.model_copy(
                update={
                    "starvation": status.starvation - starvation,
                    "dehydration": status.dehydration - dehydration,
                    "sleep": status.sleep - sleep,
                    "collapsed": status.collapsed and current <= 0,
                    "unconscious": status.unconscious and current <= 0,
                }
            )
            fp = fp.model_copy(update={"current": current, "fatigue": status})
        elif task.kind == "bandage":
            healed = min(multiplier, _wound(state, task.wound_id, target).injury)
        elif task.kind == "natural":
            check = success_roll(
                context.profile_id,
                context.ht
                + (1 if task.physician_skill is not None and task.physician_skill >= 12 else 0),
                rng=rng,
            )
            healed = multiplier if check.outcome.succeeded else 0
        else:
            if context.skill is None or context.skill < 1:
                raise ValidationError("Treatment requires a compiled medical skill")
            check = success_roll(context.profile_id, context.skill, rng=rng)
            if check.outcome is Outcome.CRITICAL_FAILURE:
                healed = -2 if task.kind == "first-aid" else -1
            elif check.outcome.succeeded:
                if task.kind == "first-aid":
                    die = 6 if check.outcome is Outcome.CRITICAL_SUCCESS else rng.randbelow(6) + 1
                    healed = max(
                        0,
                        min(
                            max(1, die + first_aid_parameters(task.technology_level)[1])
                            * multiplier,
                            _wound(state, task.wound_id, target).injury,
                        )
                        - task.bandaged_hp,
                    )
                else:
                    healed = (2 if check.outcome is Outcome.CRITICAL_SUCCESS else 1) * multiplier
        if healed < 0:
            state, _ = apply_injury(
                state,
                Wound(
                    id="medical-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    actor_id=target,
                    expected_revision=state.revision,
                    basic_damage=-healed,
                    resistance=0,
                    damage_type="cr",
                ),
                ht=context.ht,
                rng=rng,
                system=True,
            )
            hp = next(p for p in state.pools if p.id == hp.id)
        else:
            healed = min(healed, hp.maximum - hp.current)
            hp = hp.model_copy(update={"current": hp.current + healed})
        status_result: Literal["completed", "interrupted"] = (
            "interrupted" if task.status == "interrupted" else "completed"
        )
        task = task.model_copy(
            update={
                "status": status_result,
                "settled": True,
                "bandaged_hp": healed if task.kind == "bandage" else task.bandaged_hp,
            }
        )
        tasks = tuple(task if t.id == task.id else t for t in state.recovery_tasks)
        result = RecoveryResult(
            task_id=task.id,
            status=status_result,
            hp_recovered=healed,
            fp_recovered=restored,
            check=check,
            healing_die=die,
        )
    updated = state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "recovery_tasks": tasks,
            "pools": tuple(
                hp if p.id == hp.id else fp if fp is not None and p.id == fp.id else p
                for p in state.pools
            ),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=f"care:{command.id}",
                    at=state.game_time,
                    target_id=target,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(updated), result
