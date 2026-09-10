"""Bounded Basic Set recovery variants on existing recovery and hazard stores.

Numeric procedures use B423-424 and B444. Permanent crippling repair remains
fail-closed because its availability and result are explicitly setting/GM-defined.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec
from wayfarer.rules.location_types import LastingInjury
from wayfarer.rules.recovery_types import ProfileId, RecoveryTask, require_settled, retire_tasks
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.physical_traits import physical_traits
from wayfarer.simulation.resources import (
    Command,
    Pool,
    Receipt,
    Record,
    ResourceEvent,
    ResourceState,
)

_TRAUMA_PREFIX = "variant:trauma:"
_REPAIR_PREFIX = "variant:repair-lasting:"


class BeginRecoveryVariant(Command):
    kind: Literal["trauma-maintenance", "repair-lasting", "repair-permanent"]
    target_id: str
    injury_id: str | None = None


class FinishRecoveryVariant(Command):
    kind: Literal["finish-recovery-variant"] = "finish-recovery-variant"
    task_id: str


@dataclass(frozen=True)
class RecoveryVariantContext:
    profile_id: ProfileId
    ht: int
    physician_skill: int | None = None
    surgery_skill: int | None = None
    technology_level: int = 8
    life_support: bool = False
    sterile: bool = True
    anesthetic: bool = True
    equipment_quality_modifier: int = 0
    infection_risk: bool = False
    infection_modifier: int = 0


class RecoveryVariantResult(Record):
    task_id: str
    status: Literal["pending", "completed", "interrupted"]
    check: CheckTrace | None = None
    infection_check: CheckTrace | None = None
    infection_schedule_id: str | None = None
    stabilized: bool = False
    repaired: bool = False
    permanent: bool = False
    hp_lost: int = 0


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


def apply_recovery_variant(
    state: ResourceState,
    command: BeginRecoveryVariant | FinishRecoveryVariant,
    context: RecoveryVariantContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, RecoveryVariantResult]:
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
        return state, RecoveryVariantResult.model_validate_json(event.kind)
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    if type(context.ht) is not int or context.ht < 1:
        raise ValidationError("Advanced recovery requires compiled HT")

    task: RecoveryTask | None = None
    if isinstance(command, FinishRecoveryVariant):
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

    if isinstance(command, BeginRecoveryVariant):
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
                start=state.game_time,
                due=state.game_time + 7200,
                wound_id=_repair_marker(injury.id, infection_risk, context.infection_modifier),
                technology_level=context.technology_level,
                ht=context.ht,
                skill=context.surgery_skill,
                treatment_modifier=modifier,
            )
        tasks = state.recovery_tasks + (task,)
        result = RecoveryVariantResult(task_id=task.id, status="pending")
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
            result = RecoveryVariantResult(task_id=task.id, status="interrupted")
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
                result = RecoveryVariantResult(
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

                result = RecoveryVariantResult(
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
