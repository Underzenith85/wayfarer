"""Basic Set deprivation, sleep, and foraging on the authoritative clock.

Numeric timings and outcomes are independently encoded from Campaigns B426-427.
Scenario adapters supply compiled skills, physiology, terrain, and supply bindings;
none of those trusted facts are accepted in player command payloads.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.checks import CheckTrace, RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.recovery import interrupt_tasks, require_settled
from wayfarer.engine.rules.types.survival import (
    SurvivalStatus,
    SurvivalTask,
    require_survival_settled,
)
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import (
    Command,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PROFILE = "gurps-basic-set-4e-2004"
NEED_INTERVAL = 8 * 3600
QUARTER_DAY = 4 * 3600


class BeginSurvival(Command):
    kind: Literal["begin-survival"] = "begin-survival"


class SettleSurvival(Command):
    kind: Literal["settle-survival"] = "settle-survival"


class BeginSleep(Command):
    kind: Literal["begin-sleep"] = "begin-sleep"
    seconds: int = Field(ge=1, le=604800)


class BeginForage(Command):
    kind: Literal["begin-forage"] = "begin-forage"
    mode: Literal["travel", "dedicated"]


class FinishSurvivalActivity(Command):
    kind: Literal["finish-survival-activity"] = "finish-survival-activity"
    task_id: str


@dataclass(frozen=True)
class SurvivalContext:
    profile_id: str
    ht: int
    will: int
    meal_item_ids: tuple[str, ...] = ()
    water_item_ids: tuple[str, ...] = ()
    water_quarts_required: Literal[2, 3, 5] = 2
    sleep_period: int = 28800
    waking_day: int = 57600
    active: bool = True
    does_not_sleep: bool = False


@dataclass(frozen=True)
class ForagingContext:
    profile_id: str
    plant_skill: int | None
    animal_skill: int | None
    animal_method: Literal["missile", "fishing"] | None
    terrain_modifier: int
    ration_definition_id: str
    supply_owner_id: str
    party_ht: tuple[tuple[str, int], ...]


class SurvivalResult(Record):
    task_id: str
    status: Literal["pending", "completed", "interrupted"]
    meals_consumed: int = 0
    water_consumed: int = 0
    meals_produced: int = 0
    fp_lost: int = 0
    hp_lost: int = 0
    fp_recovered: int = 0
    drowsiness_check: CheckTrace | None = None
    forage_checks: tuple[CheckTrace, ...] = ()
    poison_checks: tuple[CheckTrace, ...] = ()
    poisoned_actor_ids: tuple[str, ...] = ()


def _digest(command: Command) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _replay(
    state: ResourceState, command: Command
) -> tuple[ResourceState, SurvivalResult] | None:
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Survival command ID reused")
    event = next(e for e in state.events if e.id == "survival:" + command.id)
    return state, SurvivalResult.model_validate_json(event.kind)


def _record(
    original: ResourceState,
    state: ResourceState,
    command: Command,
    result: SurvivalResult,
) -> tuple[ResourceState, SurvivalResult]:
    updated = state.model_copy(
        update={
            "revision": original.revision + 1,
            "receipts": state.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": state.events
            + (
                ResourceEvent(
                    id="survival:" + command.id,
                    at=original.game_time,
                    target_id=command.actor_id,
                    kind=result.model_dump_json(),
                ),
            ),
        }
    )
    return ResourceState.model_validate(updated), result


def _validate(
    state: ResourceState, command: Command, profile_id: str, *, system: bool
) -> tuple[ResourceState, tuple[ResourceState, SurvivalResult] | None]:
    if not system:
        raise ValidationError("Survival procedures require authoritative scenario context")
    ResourceState.model_validate(state)
    replay = _replay(state, command)
    if replay is not None:
        return state, replay
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    if profile_id != PROFILE:
        raise ValidationError("Survival procedures require the Basic Set profile")
    hp = next((p for p in state.pools if p.id == "hp:" + command.actor_id), None)
    fp = next((p for p in state.pools if p.id == "fp:" + command.actor_id), None)
    if (
        hp is None
        or hp.injury is None
        or fp is None
        or fp.fatigue is None
        or hp.injury.profile_id != PROFILE
        or fp.fatigue.profile_id != PROFILE
        or hp.injury.dead
    ):
        raise ValidationError("Survival procedures require matching living HP and FP pools")
    return state, None


def _consume(
    state: ResourceState,
    identifiers: tuple[str, ...],
    count: int,
    *,
    owner_id: str,
    marker: str,
) -> tuple[ResourceState, int]:
    items = {item.id: item for item in state.items}
    expended = list(state.expended_items)
    remaining = count
    for identifier in identifiers:
        if remaining == 0:
            break
        item = items.get(identifier)
        if (
            item is None
            or item.owner_id != owner_id
            or item.equipped
            or item.ground is not None
            or item.container_id is not None
        ):
            continue
        used = min(item.quantity, remaining)
        remaining -= used
        if used == item.quantity:
            del items[item.id]
            expended.append(item)
        else:
            items[item.id] = item.model_copy(update={"quantity": item.quantity - used})
            expended.append(
                item.model_copy(update={"id": f"spent:{marker}:{item.id}", "quantity": used})
            )
    consumed = count - remaining
    return state.model_copy(
        update={
            "items": tuple(sorted(items.values(), key=lambda item: item.id)),
            "expended_items": tuple(expended),
        }
    ), consumed


def _restricted_fatigue(
    state: ResourceState,
    command: Command,
    context: SurvivalContext,
    cause: Literal["starvation", "dehydration", "sleep"],
    amount: int,
    rng: RandomSource,
) -> tuple[ResourceState, int, int]:
    if not amount:
        return state, 0, 0
    state, result = apply_fatigue(
        state,
        FatigueCost(
            id=f"survival-fp:{command.id}:{cause}",
            actor_id=command.actor_id,
            expected_revision=state.revision,
            amount=amount,
            cause=cause,
        ),
        ht=context.ht,
        rng=rng,
        system=True,
    )
    return state, result.fp_lost, result.hp_lost


def begin_survival(
    state: ResourceState,
    command: BeginSurvival,
    context: SurvivalContext,
    *,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    if any(status.actor_id == command.actor_id for status in state.survival):
        raise ConflictError("Survival clock is already running")
    if (
        type(context.ht) is not int
        or context.ht < 1
        or type(context.will) is not int
        or context.will < 1
        or not 3600 <= context.sleep_period <= 86400
        or not 3600 <= context.waking_day <= 172800
    ):
        raise ValidationError("Survival requires compiled physiology and attributes")
    status = SurvivalStatus(
        actor_id=command.actor_id,
        started=state.game_time,
        next_meal_due=state.game_time + NEED_INTERVAL,
        next_water_due=state.game_time + NEED_INTERVAL,
        awake_since=state.game_time,
        next_sleep_due=state.game_time + context.waking_day,
        sleep_period=context.sleep_period,
        waking_day=context.waking_day,
        water_day_started=state.game_time,
        water_quarts_required=context.water_quarts_required,
        does_not_sleep=context.does_not_sleep,
    )
    state = state.model_copy(update={"survival": state.survival + (status,)})
    return _record(original, state, command, SurvivalResult(task_id=command.id, status="completed"))


def _settle_water(
    state: ResourceState,
    command: SettleSurvival,
    context: SurvivalContext,
    status: SurvivalStatus,
    rng: RandomSource,
) -> tuple[ResourceState, SurvivalStatus, int, int, int]:
    shortage = max(0, status.water_quarts_required - status.water_quarts_consumed)
    state, consumed = _consume(
        state,
        context.water_item_ids,
        shortage,
        owner_id=command.actor_id,
        marker=f"{command.id}:water",
    )
    total = status.water_quarts_consumed + consumed
    fp_loss = int(total < status.water_quarts_required)
    daily_boundary = state.game_time >= status.water_day_started + 3 * NEED_INTERVAL
    hp_loss = int(daily_boundary and total < 1)
    if daily_boundary:
        status = status.model_copy(
            update={
                "water_day_started": status.water_day_started + 3 * NEED_INTERVAL,
                "water_quarts_consumed": 0,
            }
        )
    else:
        status = status.model_copy(update={"water_quarts_consumed": total})
    status = status.model_copy(update={"next_water_due": status.next_water_due + NEED_INTERVAL})
    state, lost, spill = _restricted_fatigue(
        state, command, context, "dehydration", fp_loss + hp_loss, rng
    )
    if hp_loss:
        state, injury = apply_injury(
            state,
            Wound(
                id=f"survival-hp:{command.id}:dehydration",
                actor_id=command.actor_id,
                expected_revision=state.revision,
                basic_damage=hp_loss,
                resistance=0,
                damage_type="tox",
                injury_source="internal",
            ),
            ht=context.ht,
            rng=rng,
            system=True,
        )
        hp_loss = injury.injury
    return state, status, consumed, lost, spill + hp_loss


def _drowsiness(
    state: ResourceState,
    status: SurvivalStatus,
    command: SettleSurvival,
    context: SurvivalContext,
    rng: RandomSource,
) -> tuple[SurvivalStatus, CheckTrace | None]:
    fp = next(p for p in state.pools if p.id == "fp:" + command.actor_id)
    assert fp.fatigue is not None
    sleep_loss = fp.fatigue.sleep
    if sleep_loss * 2 < fp.maximum:
        return status.model_copy(update={"next_drowsiness_due": None}), None
    low = sleep_loss * 3 > 2 * fp.maximum
    interval = 1800 if low and not context.active else 7200
    due = status.next_drowsiness_due
    if due is None:
        return status.model_copy(update={"next_drowsiness_due": state.game_time + interval}), None
    if state.game_time < due:
        return status, None
    check = success_roll(
        PROFILE,
        context.will,
        check_modifiers(state, command.actor_id, "will"),
        rng=rng,
    )
    return status.model_copy(
        update={
            "forced_asleep": not check.outcome.succeeded,
            "drowsy_until": state.game_time + interval if check.outcome.succeeded else None,
            "next_drowsiness_due": None
            if not check.outcome.succeeded
            else state.game_time + interval,
        }
    ), check


def _credit_sleep_supplies(
    tasks: tuple[SurvivalTask, ...], actor_id: str, meals: int, water: int
) -> tuple[SurvivalTask, ...]:
    return tuple(
        task.model_copy(
            update={
                "recovery_meals_consumed": min(3, task.recovery_meals_consumed + meals),
                "recovery_water_consumed": min(
                    task.water_quarts_required,
                    task.recovery_water_consumed + water,
                ),
            }
        )
        if task.actor_id == actor_id
        and task.kind == "sleep"
        and task.status == "pending"
        and task.due - task.start >= 86400
        else task
        for task in tasks
    )


def settle_survival(
    state: ResourceState,
    command: SettleSurvival,
    context: SurvivalContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    status = next((s for s in state.survival if s.actor_id == command.actor_id), None)
    if status is None or state.game_time != status.next_due:
        raise ConflictError("Settle survival needs at the recorded deadline")
    if (
        status.water_quarts_required != context.water_quarts_required
        or status.sleep_period != context.sleep_period
        or status.waking_day != context.waking_day
        or status.does_not_sleep != context.does_not_sleep
        or type(context.ht) is not int
        or context.ht < 1
        or type(context.will) is not int
        or context.will < 1
    ):
        raise ValidationError("Survival context changed after its authoritative snapshot")
    require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
    meals = water = fp_lost = hp_lost = 0
    if state.game_time == status.next_meal_due:
        state, meals = _consume(
            state,
            context.meal_item_ids,
            1,
            owner_id=command.actor_id,
            marker=f"{command.id}:meal",
        )
        status = status.model_copy(update={"next_meal_due": status.next_meal_due + NEED_INTERVAL})
        state, lost, spill = _restricted_fatigue(
            state, command, context, "starvation", int(not meals), rng
        )
        fp_lost += lost
        hp_lost += spill
    if state.game_time == status.next_water_due:
        state, status, water, lost, injury = _settle_water(
            state, command, context, status, rng
        )
        fp_lost += lost
        hp_lost += injury
    if not context.does_not_sleep and state.game_time == status.next_sleep_due:
        sleeping = any(
            task.actor_id == command.actor_id
            and task.kind == "sleep"
            and task.status == "pending"
            and not task.settled
            for task in state.survival_tasks
        )
        status = status.model_copy(update={"next_sleep_due": status.next_sleep_due + QUARTER_DAY})
        if not sleeping:
            state, lost, spill = _restricted_fatigue(
                state, command, context, "sleep", 1, rng
            )
            fp_lost += lost
            hp_lost += spill
    sleeping = any(
        task.actor_id == command.actor_id
        and task.kind == "sleep"
        and task.status == "pending"
        and not task.settled
        for task in state.survival_tasks
    )
    status, check = (
        (status.model_copy(update={"next_drowsiness_due": None}), None)
        if sleeping
        else _drowsiness(state, status, command, context, rng)
    )
    state = state.model_copy(
        update={
            "survival": tuple(
                status if entry.actor_id == command.actor_id else entry
                for entry in state.survival
            ),
            "survival_tasks": _credit_sleep_supplies(
                state.survival_tasks, command.actor_id, meals, water
            ),
        }
    )
    result = SurvivalResult(
        task_id=command.id,
        status="completed",
        meals_consumed=meals,
        water_consumed=water,
        fp_lost=fp_lost,
        hp_lost=hp_lost,
        drowsiness_check=check,
    )
    return _record(original, state, command, result)


def begin_sleep(
    state: ResourceState,
    command: BeginSleep,
    context: SurvivalContext,
    *,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    status = next((s for s in state.survival if s.actor_id == command.actor_id), None)
    if status is None or context.does_not_sleep:
        raise ValidationError("Actor does not have a sleep requirement")
    if status.next_due <= state.game_time or any(
        t.actor_id == command.actor_id
        and not t.settled
        and t.status == "pending"
        and t.due <= state.game_time
        for t in state.survival_tasks
    ):
        raise ConflictError("Settle due survival needs or activity before sleeping")
    require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
    if any(
        t.status == "pending" and not t.settled and t.actor_id == command.actor_id
        for t in state.survival_tasks
    ) or any(
        t.status == "pending" and command.actor_id in (t.actor_id, t.target_id)
        for t in state.recovery_tasks
    ):
        raise ConflictError("Actor already has an incompatible timed activity")
    task = SurvivalTask(
        id=command.id,
        actor_id=command.actor_id,
        kind="sleep",
        start=state.game_time,
        due=state.game_time + command.seconds,
        meal_item_ids=context.meal_item_ids,
        water_item_ids=context.water_item_ids,
        water_quarts_required=context.water_quarts_required,
    )
    state = state.model_copy(update={"survival_tasks": state.survival_tasks + (task,)})
    return _record(original, state, command, SurvivalResult(task_id=task.id, status="pending"))


def _restore_sleep(
    state: ResourceState, task: SurvivalTask, status: SurvivalStatus
) -> tuple[ResourceState, SurvivalStatus, int, int, int]:
    fp = next(p for p in state.pools if p.id == "fp:" + task.actor_id)
    assert fp.fatigue is not None
    duration = task.due - task.start
    ordinary_debt = max(
        0,
        fp.maximum
        - fp.current
        - fp.fatigue.starvation
        - fp.fatigue.dehydration
        - fp.fatigue.sleep,
    )
    ordinary = min(ordinary_debt, duration // 600)
    sleep = (
        min(fp.fatigue.sleep, 1 + (duration - status.sleep_period) // 3600)
        if duration >= status.sleep_period
        else 0
    )
    meals = task.recovery_meals_consumed
    water = task.recovery_water_consumed
    starvation = dehydration = 0
    if duration >= 86400:
        state, added_meals = _consume(
            state,
            task.meal_item_ids,
            3 - meals,
            owner_id=task.actor_id,
            marker=f"{task.id}:recovery-meals",
        )
        meals += added_meals
        state, added_water = _consume(
            state,
            task.water_item_ids,
            task.water_quarts_required - water,
            owner_id=task.actor_id,
            marker=f"{task.id}:recovery-water",
        )
        water += added_water
        starvation = min(fp.fatigue.starvation, 3) if meals == 3 else 0
        dehydration = fp.fatigue.dehydration if water == task.water_quarts_required else 0
    recovered = ordinary + sleep + starvation + dehydration
    fatigue = fp.fatigue.model_copy(
        update={
            "sleep": fp.fatigue.sleep - sleep,
            "starvation": fp.fatigue.starvation - starvation,
            "dehydration": fp.fatigue.dehydration - dehydration,
            "collapsed": fp.fatigue.collapsed and fp.current + recovered <= 0,
            "unconscious": fp.fatigue.unconscious and fp.current + recovered <= 0,
        }
    )
    fp = fp.model_copy(update={"current": fp.current + recovered, "fatigue": fatigue})
    if duration >= status.sleep_period:
        status = status.model_copy(
            update={
                "awake_since": task.due,
                "next_sleep_due": task.due + status.waking_day,
                "next_drowsiness_due": None,
                "drowsy_until": None,
                "forced_asleep": False,
            }
        )
    else:
        missed = status.sleep_period - duration
        status = status.model_copy(
            update={
                "awake_since": task.due,
                "next_sleep_due": task.due + max(1, status.waking_day - 2 * missed),
                "forced_asleep": False,
            }
        )
    return state.model_copy(
        update={"pools": tuple(fp if pool.id == fp.id else pool for pool in state.pools)}
    ), status, recovered, meals, water


def finish_sleep(
    state: ResourceState,
    command: FinishSurvivalActivity,
    context: SurvivalContext,
    *,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    task = next((t for t in state.survival_tasks if t.id == command.task_id), None)
    if task is None or task.actor_id != command.actor_id or task.kind != "sleep":
        raise ValidationError("Unknown or unauthorized sleep task")
    if task.settled or (task.status == "pending" and state.game_time < task.due):
        raise ConflictError("Sleep task is not ready to settle")
    status = next(s for s in state.survival if s.actor_id == command.actor_id)
    recovered = meals = water = 0
    result_status: Literal["completed", "interrupted"] = "interrupted"
    if task.status == "pending":
        state, status, recovered, meals, water = _restore_sleep(state, task, status)
        result_status = "completed"
    task = task.model_copy(update={"status": result_status, "settled": True})
    state = state.model_copy(
        update={
            "survival": tuple(
                status if entry.actor_id == status.actor_id else entry for entry in state.survival
            ),
            "survival_tasks": tuple(
                task if entry.id == task.id else entry for entry in state.survival_tasks
            ),
        }
    )
    return _record(
        original,
        state,
        command,
        SurvivalResult(
            task_id=task.id,
            status=result_status,
            fp_recovered=recovered,
            meals_consumed=meals,
            water_consumed=water,
        ),
    )


def begin_foraging(
    state: ResourceState,
    command: BeginForage,
    context: ForagingContext,
    *,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    if (
        (context.plant_skill is None and context.animal_skill is None)
        or (context.animal_skill is not None and context.animal_method is None)
        or command.actor_id not in {actor for actor, _ in context.party_ht}
        or any(ht < 1 for _, ht in context.party_ht)
        or context.supply_owner_id not in {owner.actor_id for owner in state.owners}
    ):
        raise ValidationError("Foraging requires authored skills, party HT, and a supply owner")
    require_survival_settled(
        state.survival, state.survival_tasks, frozenset({command.actor_id}), state.game_time
    )
    require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
    if any(
        t.status == "pending" and command.actor_id in (t.actor_id, t.target_id)
        for t in state.recovery_tasks
    ):
        raise ConflictError("Foraging is incompatible with rest or treatment")
    state = state.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                state.recovery_tasks, frozenset({command.actor_id}), state.game_time
            ),
            "survival_tasks": state.survival_tasks
            + (
                SurvivalTask(
                    id=command.id,
                    actor_id=command.actor_id,
                    kind="forage",
                    start=state.game_time,
                    due=state.game_time + 86400,
                    forage_mode=command.mode,
                    plant_skill=context.plant_skill,
                    animal_skill=context.animal_skill,
                    animal_method=context.animal_method,
                    terrain_modifier=context.terrain_modifier,
                    ration_definition_id=context.ration_definition_id,
                    supply_owner_id=context.supply_owner_id,
                    party_ht=context.party_ht,
                ),
            ),
        }
    )
    return _record(original, state, command, SurvivalResult(task_id=command.id, status="pending"))


def _poison_foragers(
    state: ResourceState,
    task: SurvivalTask,
    command: FinishSurvivalActivity,
    checks: tuple[CheckTrace, ...],
    rng: RandomSource,
) -> tuple[ResourceState, tuple[CheckTrace, ...], tuple[str, ...], int]:
    poisoned: list[str] = []
    poison_checks: list[CheckTrace] = []
    hp_lost = 0
    for check_index, check in enumerate(checks):
        affected = (
            tuple(actor for actor, _ in task.party_ht)
            if check.total == 18
            else (task.actor_id,)
            if check.total == 17
            else ()
        )
        for actor in affected:
            ht = dict(task.party_ht)[actor]
            resistance = success_roll(
                PROFILE, ht, check_modifiers(state, actor, "ht"), rng=rng
            )
            poison_checks.append(resistance)
            damage = 1 if resistance.outcome.succeeded else rng.randbelow(6) + 1
            state, injury = apply_injury(
                state,
                Wound(
                    id=f"forage-poison:{command.id}:{check_index}:{actor}",
                    actor_id=actor,
                    expected_revision=state.revision,
                    basic_damage=damage,
                    resistance=0,
                    damage_type="tox",
                    injury_source="internal",
                ),
                ht=ht,
                rng=rng,
                system=True,
            )
            hp_lost += injury.injury
            poisoned.append(actor)
    return state, tuple(poison_checks), tuple(poisoned), hp_lost


def finish_foraging(
    state: ResourceState,
    command: FinishSurvivalActivity,
    context: ForagingContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, SurvivalResult]:
    original, replay = _validate(state, command, context.profile_id, system=system)
    if replay is not None:
        return replay
    task = next((t for t in state.survival_tasks if t.id == command.task_id), None)
    if task is None or task.actor_id != command.actor_id or task.kind != "forage":
        raise ValidationError("Unknown or unauthorized foraging task")
    if task.settled or (task.status == "pending" and state.game_time < task.due):
        raise ConflictError("Foraging task is not ready to settle")
    if task.status == "interrupted":
        task = task.model_copy(update={"settled": True})
        state = state.model_copy(
            update={
                "survival_tasks": tuple(
                    task if entry.id == task.id else entry for entry in state.survival_tasks
                )
            }
        )
        return _record(
            original,
            state,
            command,
            SurvivalResult(task_id=task.id, status="interrupted"),
        )
    count = 1 if task.forage_mode == "travel" else 5
    checks: list[CheckTrace] = []
    meals = 0
    if task.plant_skill is not None:
        for _ in range(count):
            check = success_roll(
                PROFILE,
                task.plant_skill + task.terrain_modifier,
                check_modifiers(state, task.actor_id, "iq"),
                rng=rng,
            )
            checks.append(check)
            meals += int(check.outcome.succeeded)
    plant_checks = tuple(checks)
    if task.animal_skill is not None:
        target = task.animal_skill + task.terrain_modifier - (
            4 if task.animal_method == "missile" else 0
        )
        for _ in range(count):
            check = success_roll(
                PROFILE, target, check_modifiers(state, task.actor_id, "dx"), rng=rng
            )
            checks.append(check)
            meals += 2 * int(check.outcome.succeeded)
    state, poison_checks, poisoned, hp_lost = _poison_foragers(
        state, task, command, plant_checks, rng
    )
    if meals:
        ration_id = "forage:" + task.id + ":rations"
        if ration_id in {item.id for item in state.items + state.expended_items}:
            raise ConflictError("Foraging output identifier already exists")
        state = state.model_copy(
            update={
                "items": state.items
                + (
                    Item(
                        id=ration_id,
                        definition_id=task.ration_definition_id or "",
                        owner_id=task.supply_owner_id or "",
                        quantity=meals,
                    ),
                )
            }
        )
    task = task.model_copy(update={"status": "completed", "settled": True})
    state = state.model_copy(
        update={
            "survival_tasks": tuple(
                task if entry.id == task.id else entry for entry in state.survival_tasks
            )
        }
    )
    return _record(
        original,
        state,
        command,
        SurvivalResult(
            task_id=task.id,
            status="completed",
            meals_produced=meals,
            hp_lost=hp_lost,
            forage_checks=tuple(checks),
            poison_checks=poison_checks,
            poisoned_actor_ids=poisoned,
        ),
    )
