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
from wayfarer.models import Record
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import blocked_fp, blocked_hp, require_hazards_settled
from wayfarer.rules.recovery_types import (
    ProfileId,
    RecoveryTask,
    require_settled,
    rest_entitlement,
    retire_tasks,
)
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.injury import InjuryResult, Wound, apply_injury
from wayfarer.simulation.resources import Command, Receipt, ResourceEvent, ResourceState


class BeginRecovery(Command):
    kind: Literal[
        "rest",
        "natural",
        "bandage",
        "first-aid",
        "physician",
        "resuscitate",
        "stabilize",
        "mortal-check",
    ]
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
    treatment_modifier: int = 0
    surgical_facility: bool = False


class RecoveryResult(Record):
    task_id: str
    status: Literal["pending", "completed", "interrupted"]
    hp_recovered: int = 0
    fp_recovered: int = 0
    check: CheckTrace | None = None
    healing_die: int | None = None
    resuscitated: bool = False
    stabilized: bool = False


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


def accrue_rest(state: ResourceState, at: int) -> ResourceState:
    """Deterministic clock accrual, preserving continuous restricted-fatigue rest."""
    pools = {p.id: p for p in state.pools}
    tasks: list[RecoveryTask] = []
    for task in state.recovery_tasks:
        if task.kind != "rest" or task.settled:
            tasks.append(task)
            continue
        fp = pools.get(f"fp:{task.target_id}")
        if fp is None or fp.fatigue is None or fp.fatigue.heart_attack:
            tasks.append(task)
            continue
        status = fp.fatigue
        granted = (
            task.ordinary_granted,
            task.starvation_granted,
            task.dehydration_granted,
            task.sleep_granted,
        )
        earned = rest_entitlement(task, at)
        available = (
            max(
                0,
                fp.maximum
                - fp.current
                - status.starvation
                - status.dehydration
                - status.sleep
                - blocked_fp(state.illnesses, task.target_id),
            ),
            status.starvation,
            status.dehydration,
            status.sleep,
        )
        award = tuple(
            min(max(0, e - g), remaining)
            for e, g, remaining in zip(earned, granted, available, strict=True)
        )
        current = fp.current + sum(award)
        status = status.model_copy(
            update={
                "power": max(
                    0, status.power - max(0, award[0] - max(0, available[0] - status.power))
                ),
                "starvation": status.starvation - award[1],
                "dehydration": status.dehydration - award[2],
                "sleep": status.sleep - award[3],
                "collapsed": status.collapsed and current <= 0,
                "unconscious": status.unconscious and current <= 0,
            }
        )
        pools[fp.id] = fp.model_copy(update={"current": current, "fatigue": status})
        # Mark earned units consumed even if another healing source filled FP.
        # They can never become credit against a future fatigue cost.
        task = task.model_copy(
            update={
                "ordinary_granted": earned[0],
                "starvation_granted": earned[1],
                "dehydration_granted": earned[2],
                "sleep_granted": earned[3],
                "fp_recovered_total": task.fp_recovered_total + sum(award),
            }
        )
        tasks.append(task)
    return state.model_copy(update={"pools": tuple(pools.values()), "recovery_tasks": tuple(tasks)})


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
    kind = command.kind if isinstance(command, BeginRecovery) else task.kind if task else ""
    if hp.injury.dead or (
        hp.injury.mortal_wound and kind not in ("stabilize", "mortal-check", "resuscitate")
    ):
        raise ValidationError("Dead or mortally wounded patients require separate treatment")
    if kind in ("stabilize", "mortal-check") and (
        context.profile_id != "gurps-basic-set-4e-2004" or not hp.injury.mortal_wound
    ):
        raise ValidationError("This procedure requires a Basic Set mortal wound")
    if kind == "resuscitate":
        if (
            context.profile_id != "gurps-basic-set-4e-2004"
            or fp is None
            or fp.fatigue is None
            or not fp.fatigue.heart_attack
            or fp.fatigue.heart_attack_deadline is None
            or state.game_time >= fp.fatigue.heart_attack_deadline
        ):
            raise ValidationError(
                "Resuscitation requires a living heart-attack patient before deadline"
            )
    elif (
        fp is not None
        and fp.fatigue is not None
        and fp.fatigue.heart_attack
        and kind not in ("stabilize", "mortal-check")
    ):
        raise ValidationError("Heart attack requires resuscitation, not ordinary recovery")
    check = None
    die = None
    healed = restored = 0
    resuscitated = False
    stabilized = False
    if isinstance(command, BeginRecovery) and command.kind == "mortal-check":
        if hp.injury.mortal_wound_due is None or state.game_time != hp.injury.mortal_wound_due:
            raise ValidationError("Mortal-wound survival check is not due")
        check = success_roll(
            context.profile_id,
            context.ht + hp.injury.physical_traits.fitness,
            check_modifiers(state, target, "ht"),
            rng=rng,
        )
        stabilized = check.outcome is Outcome.CRITICAL_SUCCESS
        dead = not check.outcome.succeeded
        hp = hp.model_copy(
            update={
                "injury": hp.injury.model_copy(
                    update={
                        "dead": dead,
                        "mortal_wound": not stabilized,
                        "unconscious": hp.injury.unconscious or stabilized,
                        "mortal_wound_due": None if dead or stabilized else state.game_time + 1800,
                    }
                )
            }
        )
        tasks = (
            retire_tasks(state.recovery_tasks, frozenset({target}), state.game_time)
            if dead or stabilized
            else state.recovery_tasks
        )
        result = RecoveryResult(
            task_id=command.id, status="completed", check=check, stabilized=stabilized
        )
    elif isinstance(command, BeginRecovery):
        assert command.kind != "mortal-check"
        assert hp.injury is not None
        if command.kind not in ("resuscitate", "stabilize"):
            require_hazards_settled(
                state.hazards, frozenset({command.actor_id, target}), state.game_time
            )
        require_settled(
            state.recovery_tasks, frozenset({command.actor_id, target}), state.game_time
        )
        if any(
            t.status == "pending"
            and ({t.actor_id, t.target_id} & {command.actor_id, target})
            and not (
                t.kind == command.kind == "physician"
                and t.actor_id == command.actor_id
                and t.target_id != target
            )
            for t in state.recovery_tasks
        ):
            raise ConflictError("Actor or patient already has pending recovery")
        duration = command.seconds
        bandaged = 0
        modifier = context.treatment_modifier
        if command.kind == "resuscitate":
            if context.technology_level < 7 or command.actor_id == target:
                raise ValidationError("Heart-attack resuscitation requires another TL7+ caregiver")
            assert fp is not None and fp.fatigue is not None
            assert fp.fatigue.heart_attack_deadline is not None
            duration = 60
            if state.game_time + duration >= fp.fatigue.heart_attack_deadline:
                raise ValidationError("Resuscitation cannot finish before the fatal deadline")
        elif command.kind == "stabilize":
            if (
                context.technology_level < 6
                or not context.surgical_facility
                or command.actor_id == target
            ):
                raise ValidationError(
                    "Stabilization requires another surgeon and a sterile TL6+ facility"
                )
            if hp.injury.mortal_wound_due is None or state.game_time >= hp.injury.mortal_wound_due:
                raise ValidationError("Settle the patient's survival check before surgery")
            duration = 3600
            modifier += context.technology_level - 6
            modifier -= (
                4 if hp.current <= -4 * hp.maximum else 2 if hp.current <= -3 * hp.maximum else 0
            )
            modifier -= 2 * sum(
                t.kind == "stabilize"
                and t.target_id == target
                and t.status == "completed"
                and t.start >= hp.injury.mortal_wound_started
                for t in state.recovery_tasks
            )
        elif command.kind in ("bandage", "first-aid"):
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
        if command.kind in ("first-aid", "physician", "resuscitate", "stabilize") and (
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
        entitled = [0, 0, 0, 0]
        if command.kind == "rest":
            assert fp is not None and fp.fatigue is not None
            fatigue = fp.fatigue
            entitled = [
                fp.maximum - fp.current - fatigue.starvation - fatigue.dehydration - fatigue.sleep,
                fatigue.starvation,
                fatigue.dehydration,
                fatigue.sleep,
            ]
            for earlier in state.recovery_tasks:
                if (
                    earlier.kind == "rest"
                    and earlier.target_id == target
                    and not earlier.settled
                    and (earlier.due <= state.game_time or earlier.status == "interrupted")
                ):
                    outstanding = tuple(
                        max(0, e - g)
                        for e, g in zip(
                            rest_entitlement(earlier),
                            (
                                earlier.ordinary_granted,
                                earlier.starvation_granted,
                                earlier.dehydration_granted,
                                earlier.sleep_granted,
                            ),
                            strict=True,
                        )
                    )
                    entitled = [
                        max(0, debt - earned)
                        for debt, earned in zip(entitled, outstanding, strict=True)
                    ]
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
            ht=context.ht,
            skill=context.skill,
            treatment_modifier=modifier,
            fp_interval=300 if hp.injury.physical_traits.fitness else 600,
            power_entitlement=min(entitled[0], fp.fatigue.power)
            if fp is not None and fp.fatigue is not None
            else 0,
            healing_bonus=hp.injury.physical_traits.fitness
            + (5 if hp.injury.physical_traits.healing else 0),
            healing_rate=2 if hp.injury.physical_traits.healing == 2 else 1,
            ordinary_entitlement=entitled[0],
            starvation_entitlement=entitled[1],
            dehydration_entitlement=entitled[2],
            sleep_entitlement=entitled[3],
            hp_entitlement=hp.maximum - hp.current,
        )
        tasks = state.recovery_tasks + (task,)
        result = RecoveryResult(task_id=task.id, status="pending")
    else:
        assert task is not None
        if task.settled or task.status == "completed":
            raise ConflictError("Recovery task is no longer pending")
        if task.kind == "rest" and task.status == "pending" and state.game_time < task.due:
            if state.game_time <= task.start:
                raise ValidationError("Recovery is not due in this profile")
            task = task.model_copy(
                update={"status": "interrupted", "interrupted_at": state.game_time}
            )
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
            restricted = status.starvation + status.dehydration + status.sleep
            earned = rest_entitlement(task)
            ordinary = min(
                max(0, earned[0] - task.ordinary_granted),
                max(
                    0,
                    fp.maximum
                    - fp.current
                    - restricted
                    - blocked_fp(state.illnesses, task.target_id),
                ),
            )
            starvation = min(max(0, earned[1] - task.starvation_granted), status.starvation)
            dehydration = min(max(0, earned[2] - task.dehydration_granted), status.dehydration)
            sleep = min(max(0, earned[3] - task.sleep_granted), status.sleep)
            current = fp.current + ordinary + starvation + dehydration + sleep
            restored = ordinary + starvation + dehydration + sleep + task.fp_recovered_total
            status = status.model_copy(
                update={
                    "power": max(
                        0,
                        status.power
                        - max(
                            0,
                            ordinary - max(0, fp.maximum - fp.current - restricted - status.power),
                        ),
                    ),
                    "starvation": status.starvation - starvation,
                    "dehydration": status.dehydration - dehydration,
                    "sleep": status.sleep - sleep,
                    "collapsed": status.collapsed and current <= 0,
                    "unconscious": status.unconscious and current <= 0,
                }
            )
            fp = fp.model_copy(update={"current": current, "fatigue": status})
        elif task.kind == "resuscitate":
            if task.skill is None:
                raise ValidationError("Resuscitation requires a compiled medical skill")
            check = success_roll(
                context.profile_id,
                task.skill + task.treatment_modifier,
                check_modifiers(state, task.actor_id, "iq"),
                rng=rng,
            )
            if check.outcome.succeeded:
                assert fp is not None and fp.fatigue is not None
                fp = fp.model_copy(
                    update={
                        "fatigue": fp.fatigue.model_copy(
                            update={
                                "heart_attack": False,
                                "heart_attack_deadline": None,
                            }
                        )
                    }
                )
                resuscitated = True
                hp = hp.model_copy(update={"current": min(0, hp.current)})
        elif task.kind == "stabilize":
            assert hp.injury is not None
            if hp.injury.mortal_wound_due is None or state.game_time >= hp.injury.mortal_wound_due:
                raise ValidationError("Settle the patient's survival check before surgery finishes")
            assert task.skill is not None
            check = success_roll(
                context.profile_id,
                task.skill + task.treatment_modifier,
                check_modifiers(state, task.actor_id, "iq"),
                rng=rng,
            )
            if check.outcome.succeeded:
                hp = hp.model_copy(
                    update={
                        "injury": hp.injury.model_copy(
                            update={
                                "mortal_wound": False,
                                "mortal_wound_due": None,
                                "unconscious": True,
                            }
                        )
                    }
                )
                stabilized = True
            else:
                healed = -sum(rng.randbelow(6) + 1 for _ in range(3))
        elif task.kind == "bandage":
            healed = min(multiplier, _wound(state, task.wound_id, target).injury)
        elif task.kind == "natural":
            check = success_roll(
                context.profile_id,
                task.ht
                + task.healing_bonus
                + (1 if task.physician_skill is not None and task.physician_skill >= 12 else 0),
                check_modifiers(state, target, "ht"),
                rng=rng,
            )
            healed = multiplier * task.healing_rate if check.outcome.succeeded else 0
        else:
            if task.skill is None or task.skill < 1:
                raise ValidationError("Treatment requires a compiled medical skill")
            check = success_roll(
                context.profile_id, task.skill, check_modifiers(state, task.actor_id, "iq"), rng=rng
            )
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
            state = state.model_copy(
                update={
                    "recovery_tasks": tuple(
                        t.model_copy(update={"status": "completed", "settled": True})
                        if t.id == task.id
                        else t
                        for t in state.recovery_tasks
                    )
                }
            )
            state, _ = apply_injury(
                state,
                Wound(
                    id="medical-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    actor_id=target,
                    expected_revision=state.revision,
                    basic_damage=-healed,
                    resistance=0,
                    damage_type="cr",
                    injury_source="internal",
                ),
                ht=task.ht,
                rng=rng,
                system=True,
            )
            hp = next(p for p in state.pools if p.id == hp.id)
        else:
            healed = min(
                healed,
                max(0, hp.maximum - hp.current - blocked_hp(state.illnesses, target, task.kind)),
                task.hp_entitlement,
            )
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
            resuscitated=resuscitated,
            stabilized=stabilized,
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
