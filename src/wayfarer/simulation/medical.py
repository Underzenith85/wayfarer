"""Timed recovery and treatment with durable, per-wound attempt limits.

Numeric source: Basic Set Campaigns fourth printing, B424-427. Tasks use
ResourceState.game_time in seconds and never move the shared clock themselves.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import (
    HazardSchedule,
    HazardSpec,
    blocked_fp,
    blocked_hp,
    require_hazards_settled,
)
from wayfarer.rules.location_types import LastingInjury
from wayfarer.rules.recovery_types import (
    ProfileId,
    RecoveryTask,
    require_settled,
    rest_entitlement,
    retire_tasks,
)
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.injury import InjuryResult, Wound, apply_injury
from wayfarer.simulation.physical_traits import physical_traits
from wayfarer.simulation.resources import Command, Pool, Receipt, ResourceEvent, ResourceState


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
        "trauma-maintenance",
        "repair-lasting",
        "repair-permanent",
    ]
    target_id: str
    wound_id: str | None = None
    injury_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    seconds: int = Field(default=600, ge=1, le=604800)


class FinishRecovery(Command):
    kind: Literal["finish-recovery"] = "finish-recovery"
    task_id: str

    @model_validator(mode="before")
    @classmethod
    def migrate_finish_kind(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("kind") == "finish-recovery-variant":
            return {**value, "kind": "finish-recovery"}
        return value


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
    surgery_skill: int | None = None
    life_support: bool = False
    sterile: bool = True
    anesthetic: bool = True
    equipment_quality_modifier: int = 0
    infection_risk: bool = False
    infection_modifier: int = 0


class RecoveryResult(Record):
    task_id: str
    status: Literal["pending", "completed", "interrupted"]
    hp_recovered: int = 0
    fp_recovered: int = 0
    check: CheckTrace | None = None
    healing_die: int | None = None
    resuscitated: bool = False
    stabilized: bool = False
    infection_check: CheckTrace | None = Field(default=None, exclude_if=lambda v: v is None)
    infection_schedule_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    repaired: bool = Field(default=False, exclude_if=lambda v: not v)
    permanent: bool = Field(default=False, exclude_if=lambda v: not v)
    hp_lost: int = Field(default=0, exclude_if=lambda v: v == 0)


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
    # Procedure dispatch shares the public entity set and durable task discriminator.
    task = next(
        (
            t
            for t in state.recovery_tasks
            if isinstance(command, FinishRecovery) and t.id == command.task_id
        ),
        None,
    )
    procedure = (
        command.kind if isinstance(command, BeginRecovery) else task.procedure if task else None
    )
    if procedure in ("trauma-maintenance", "repair-lasting", "repair-permanent"):
        return _apply_advanced_recovery(state, command, context, rng=rng, system=system)
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
        assert command.kind in (
            "rest",
            "natural",
            "bandage",
            "first-aid",
            "physician",
            "resuscitate",
            "stabilize",
        )
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


_TRAUMA_PREFIX = "variant:trauma:"
_REPAIR_PREFIX = "variant:repair-lasting:"


def surgery_equipment_modifier(tl: int) -> int:
    """Basic-equipment modifier from B424, excluding quality and sterility."""
    if not 1 <= tl <= 12:
        raise ValidationError("Surgery requires TL1 or later")
    if tl == 1:
        return -6
    if tl in (2, 3):
        return -5
    if tl == 4:
        return -4
    if tl == 5:
        return -2
    return tl - 6


def _trauma_marker(life_support: bool) -> str:
    return _TRAUMA_PREFIX + ("daily" if life_support else "hourly")


def _repair_marker(injury_id: str, infection_risk: bool, infection_modifier: int) -> str:
    risk = "risk" if infection_risk else "clean"
    return f"{_REPAIR_PREFIX}{risk}:{infection_modifier}:{injury_id}"


def _repair_metadata(marker: str | None) -> tuple[bool, int, str]:
    if marker is None or not marker.startswith(_REPAIR_PREFIX):
        raise ValidationError("Advanced recovery task marker is invalid")
    payload = marker.removeprefix(_REPAIR_PREFIX)
    pieces = payload.split(":", 2)
    if len(pieces) != 3 or pieces[0] not in ("risk", "clean") or not pieces[2]:
        raise ValidationError("Advanced recovery task marker is invalid")
    try:
        modifier = int(pieces[1])
    except ValueError as error:
        raise ValidationError("Advanced recovery task marker is invalid") from error
    if not -30 <= modifier <= 30:
        raise ValidationError("Advanced recovery task marker is invalid")
    return pieces[0] == "risk", modifier, pieces[2]


def _hp(state: ResourceState, actor_id: str, profile_id: ProfileId) -> Pool:
    hp = next((p for p in state.pools if p.id == f"hp:{actor_id}"), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != profile_id:
        raise ValidationError("Advanced recovery requires matching profile HP")
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Advanced recovery variants require the Basic Set profile")
    return hp


def _lasting_injury(state: ResourceState, target: str, injury_id: str | None) -> LastingInjury:
    hp = next(p for p in state.pools if p.id == f"hp:{target}")
    assert hp.injury is not None
    injury = next((w for w in hp.injury.lasting_injuries if w.id == injury_id), None)
    if injury is None:
        raise ValidationError("Surgery requires the recorded crippling injury")
    return injury


def _replace_lasting(hp: Pool, replacement: LastingInjury) -> Pool:
    assert hp.injury is not None
    status = hp.injury.model_copy(
        update={
            "lasting_injuries": tuple(
                replacement if w.id == replacement.id else w for w in hp.injury.lasting_injuries
            )
        }
    )
    return hp.model_copy(update={"injury": status})


def _infection_schedule(task: RecoveryTask, now: int, infection_modifier: int) -> HazardSchedule:
    schedule_id = f"infection:{task.id}"
    spec = HazardSpec(
        id=schedule_id,
        kind="disease",
        scene_id="postoperative-care",
        interval=86400,
        cycles=100000,
        resistance_modifier=infection_modifier,
        damage_dice=0,
        damage_add=1,
        resistible=True,
        reference="Basic Set B444 postoperative infection",
        recovery_successes=1,
    )
    return HazardSchedule(
        id=schedule_id,
        actor_id=task.target_id,
        spec=spec,
        started=now,
        due=now + 86400,
        remaining=spec.cycles,
        ht=task.ht,
        will=task.ht,
        swimming=1,
    )


def _apply_advanced_recovery(
    state: ResourceState,
    command: BeginRecovery | FinishRecovery,
    context: CareContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, RecoveryResult]:
    """Resolve one bounded advanced-recovery command with durable replay."""
    if not system:
        raise ValidationError("Advanced recovery requires authoritative treatment context")
    ResourceState.model_validate(state)
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Advanced recovery command ID reused")
        event = next(e for e in state.events if e.id == f"care:{command.id}")
        return state, RecoveryResult.model_validate_json(event.kind)
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    if type(context.ht) is not int or context.ht < 1:
        raise ValidationError("Advanced recovery requires compiled HT")

    task: RecoveryTask | None = None
    if isinstance(command, FinishRecovery):
        task = next((t for t in state.recovery_tasks if t.id == command.task_id), None)
        if task is None or task.actor_id != command.actor_id:
            raise ValidationError("Unknown or unauthorized advanced recovery task")
        target = task.target_id
    else:
        target = command.target_id
    hp = _hp(state, target, context.profile_id)
    assert hp.injury is not None
    if hp.injury.dead:
        raise ValidationError("Dead patients cannot receive this recovery procedure")

    check = None
    infection_check = None
    infection_schedule_id = None
    stabilized = repaired = permanent = False
    hp_lost = 0

    if isinstance(command, BeginRecovery):
        if command.kind == "repair-permanent":
            injury = _lasting_injury(state, target, command.injury_id)
            if injury.duration != "permanent":
                raise ValidationError(
                    "Permanent-repair selection requires a permanent crippling injury"
                )
            raise ValidationError(
                "Permanent crippling repair is setting-defined and unsupported without an authored procedure"
            )
        require_settled(
            state.recovery_tasks, frozenset({command.actor_id, target}), state.game_time
        )
        if any(
            t.status == "pending"
            and not t.settled
            and ({t.actor_id, t.target_id} & {command.actor_id, target})
            for t in state.recovery_tasks
        ):
            raise ConflictError("Actor or patient already has pending recovery")

        if command.kind == "trauma-maintenance":
            if (
                context.technology_level < 6
                or command.actor_id == target
                or context.physician_skill is None
                or context.physician_skill < 1
                or not hp.injury.mortal_wound
                or hp.injury.mortal_wound_due is None
                or state.game_time >= hp.injury.mortal_wound_due
            ):
                raise ValidationError(
                    "Trauma maintenance requires a living mortal-wound patient and a TL6+ Physician caregiver"
                )
            duration = 86400 if context.life_support else 3600
            task = RecoveryTask(
                id=command.id,
                actor_id=command.actor_id,
                target_id=target,
                profile_id=context.profile_id,
                # Use an existing contract kind; the marker makes this task exclusive
                # to this reducer, while generic Physician recovery rejects mortal wounds.
                kind="physician",
                procedure="trauma-maintenance",
                start=state.game_time,
                due=state.game_time + duration,
                wound_id=_trauma_marker(context.life_support),
                technology_level=context.technology_level,
                ht=context.ht,
                skill=context.physician_skill,
            )
            hp = hp.model_copy(
                update={"injury": hp.injury.model_copy(update={"mortal_wound_due": task.due})}
            )
        else:
            injury = _lasting_injury(state, target, command.injury_id)
            if (
                injury.duration != "lasting"
                or injury.recovery_at is None
                or injury.recovery_at <= state.game_time
            ):
                raise ValidationError(
                    "Lasting-injury surgery requires an active lasting crippling injury"
                )
            if (
                command.actor_id == target
                or context.surgery_skill is None
                or context.surgery_skill < 1
            ):
                raise ValidationError(
                    "Lasting-injury repair requires another character with Surgery"
                )
            modifier = (
                surgery_equipment_modifier(context.technology_level)
                + context.equipment_quality_modifier
                + (-3 if not context.sterile else 0)
                + (-2 if context.technology_level >= 5 and not context.anesthetic else 0)
            )
            infection_risk = context.technology_level < 5 or (
                context.technology_level == 5 and context.infection_risk
            )
            task = RecoveryTask(
                id=command.id,
                actor_id=command.actor_id,
                target_id=target,
                profile_id=context.profile_id,
                # Generic stabilization rejects a non-mortal patient, preventing
                # this specialized task from being settled through the wrong reducer.
                kind="stabilize",
                procedure="repair-lasting",
                start=state.game_time,
                due=state.game_time + 7200,
                wound_id=_repair_marker(injury.id, infection_risk, context.infection_modifier),
                technology_level=context.technology_level,
                ht=context.ht,
                skill=context.surgery_skill,
                treatment_modifier=modifier,
            )
        tasks = state.recovery_tasks + (task,)
        result = RecoveryResult(task_id=task.id, status="pending")
    else:
        assert task is not None
        trauma = task.wound_id is not None and task.wound_id.startswith(_TRAUMA_PREFIX)
        repair = task.wound_id is not None and task.wound_id.startswith(_REPAIR_PREFIX)
        if not ((trauma and task.kind == "physician") or (repair and task.kind == "stabilize")):
            raise ValidationError("Task is not an advanced recovery variant")
        if task.settled or task.status == "completed":
            raise ConflictError("Advanced recovery task is no longer pending")
        if task.profile_id != context.profile_id:
            raise ValidationError("Recovery profile changed")
        if task.status == "interrupted":
            if trauma:
                assert hp.injury is not None and task.interrupted_at is not None
                fallback_due = task.interrupted_at + 1800
                if state.game_time > fallback_due:
                    raise ConflictError(
                        "Settle interrupted trauma maintenance before its survival deadline"
                    )
                hp = hp.model_copy(
                    update={
                        "injury": hp.injury.model_copy(update={"mortal_wound_due": fallback_due})
                    }
                )
            task = task.model_copy(update={"settled": True})
            tasks = tuple(task if t.id == task.id else t for t in state.recovery_tasks)
            result = RecoveryResult(task_id=task.id, status="interrupted")
        else:
            if state.game_time != task.due:
                raise ValidationError("Advanced recovery must settle at its shared-clock deadline")
            if trauma:
                assert task.skill is not None and task.wound_id is not None
                patient_ht = task.ht + physical_traits(state, task.target_id).fitness
                patient = check_modifiers(state, target, "ht")
                physician = check_modifiers(state, task.actor_id, "iq")
                use_physician = task.skill + sum(m.value for m in physician) > patient_ht + sum(
                    m.value for m in patient
                )
                check = success_roll(
                    context.profile_id,
                    task.skill if use_physician else patient_ht,
                    physician if use_physician else patient,
                    rng=rng,
                )
                if check.outcome is Outcome.CRITICAL_SUCCESS:
                    assert hp.injury is not None
                    stabilized = True
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
                elif check.outcome.succeeded:
                    assert hp.injury is not None
                    interval = 86400 if task.wound_id == _trauma_marker(True) else 3600
                    hp = hp.model_copy(
                        update={
                            "injury": hp.injury.model_copy(
                                update={"mortal_wound_due": state.game_time + interval}
                            )
                        }
                    )
                else:
                    assert hp.injury is not None
                    hp = hp.model_copy(
                        update={"injury": hp.injury.model_copy(update={"dead": True})}
                    )
                task = task.model_copy(update={"status": "completed", "settled": True})
                tasks = tuple(task if t.id == task.id else t for t in state.recovery_tasks)
                assert hp.injury is not None
                if hp.injury.dead or stabilized:
                    tasks = retire_tasks(tasks, frozenset({target}), state.game_time)
                result = RecoveryResult(
                    task_id=task.id,
                    status="completed",
                    check=check,
                    stabilized=stabilized,
                )
            else:
                assert task.skill is not None
                infection_risk, infection_modifier, injury_id = _repair_metadata(task.wound_id)
                injury = _lasting_injury(state, target, injury_id)
                if injury.duration != "lasting" or injury.recovery_at is None:
                    raise ValidationError("Recorded lasting injury is no longer repairable")
                check = success_roll(
                    context.profile_id,
                    task.skill + task.treatment_modifier,
                    check_modifiers(state, task.actor_id, "iq"),
                    rng=rng,
                )
                if check.outcome.succeeded:
                    remaining = max(1, injury.recovery_at - state.game_time)
                    # The source converts remaining months to weeks. The injury
                    # clock models one month as 30 days, so preserve the numeric
                    # remaining count while changing the unit to seven days.
                    shortened = max(1, (remaining * 7 + 29) // 30)
                    injury = injury.model_copy(update={"recovery_at": state.game_time + shortened})
                    hp = _replace_lasting(hp, injury)
                    repaired = True
                else:
                    if check.outcome is Outcome.CRITICAL_FAILURE:
                        injury = injury.model_copy(
                            update={"duration": "permanent", "recovery_at": None}
                        )
                        hp = _replace_lasting(hp, injury)
                        permanent = True
                    hp_lost = sum(rng.randbelow(6) + 1 for _ in range(3))

                task = task.model_copy(update={"status": "completed", "settled": True})
                tasks = tuple(task if t.id == task.id else t for t in state.recovery_tasks)
                state = state.model_copy(
                    update={
                        "pools": tuple(hp if p.id == hp.id else p for p in state.pools),
                        "recovery_tasks": tasks,
                    }
                )
                if hp_lost:
                    state, _ = apply_injury(
                        state,
                        Wound(
                            id="variant-hp:" + hashlib.sha256(command.id.encode()).hexdigest(),
                            actor_id=target,
                            expected_revision=state.revision,
                            basic_damage=hp_lost,
                            resistance=0,
                            damage_type="cr",
                        ),
                        ht=task.ht,
                        rng=rng,
                        system=True,
                    )
                    hp = next(p for p in state.pools if p.id == hp.id)
                    tasks = state.recovery_tasks

                if infection_risk and hp.injury is not None and not hp.injury.dead:
                    infection_check = success_roll(
                        context.profile_id,
                        max(
                            1,
                            task.ht
                            + physical_traits(state, task.target_id).fitness
                            + 3
                            + infection_modifier,
                        ),
                        check_modifiers(state, target, "ht"),
                        rng=rng,
                    )
                    if not infection_check.outcome.succeeded:
                        schedule = _infection_schedule(task, state.game_time, infection_modifier)
                        if any(h.id == schedule.id for h in state.hazards):
                            raise ConflictError("Postoperative infection schedule already exists")
                        state = state.model_copy(update={"hazards": state.hazards + (schedule,)})
                        infection_schedule_id = schedule.id

                result = RecoveryResult(
                    task_id=task.id,
                    status="completed",
                    check=check,
                    infection_check=infection_check,
                    infection_schedule_id=infection_schedule_id,
                    repaired=repaired,
                    permanent=permanent,
                    hp_lost=hp_lost,
                )

    updated = state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "recovery_tasks": tasks,
            "pools": tuple(hp if p.id == hp.id else p for p in state.pools),
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
