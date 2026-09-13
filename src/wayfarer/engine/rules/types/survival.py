"""Persistent Basic Set survival clocks and time-bound activity records."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


class SurvivalStatus(Record):
    actor_id: str
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    started: int = Field(ge=0)
    next_meal_due: int = Field(ge=1)
    next_water_due: int = Field(ge=1)
    awake_since: int = Field(ge=0)
    next_sleep_due: int = Field(ge=1)
    sleep_period: int = Field(default=28800, ge=3600, le=86400)
    waking_day: int = Field(default=57600, ge=3600, le=172800)
    water_day_started: int = Field(ge=0)
    water_quarts_required: Literal[2, 3, 5] = 2
    water_quarts_consumed: int = Field(default=0, ge=0, le=5)
    next_drowsiness_due: int | None = Field(default=None, ge=0)
    drowsy_until: int | None = Field(default=None, ge=0)
    forced_asleep: bool = False
    does_not_sleep: bool = False

    @model_validator(mode="after")
    def validate_timeline(self) -> SurvivalStatus:
        if min(self.next_meal_due, self.next_water_due, self.next_sleep_due) <= self.started:
            raise ValueError("Survival deadlines must follow their start")
        if self.water_day_started < self.started or self.awake_since < self.started:
            raise ValueError("Survival periods cannot predate their schedule")
        return self

    @property
    def next_due(self) -> int:
        values = [self.next_meal_due, self.next_water_due]
        if not self.does_not_sleep:
            values.append(self.next_sleep_due)
        if self.next_drowsiness_due is not None:
            values.append(self.next_drowsiness_due)
        return min(values)


class SurvivalTask(Record):
    id: str = Field(min_length=1, max_length=200)
    actor_id: str
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    kind: Literal["sleep", "forage"]
    start: int = Field(ge=0)
    due: int = Field(ge=1)
    status: Literal["pending", "completed", "interrupted"] = "pending"
    interrupted_at: int | None = Field(default=None, ge=0)
    settled: bool = False
    forage_mode: Literal["travel", "dedicated"] | None = None
    plant_skill: int | None = Field(default=None, ge=1)
    animal_skill: int | None = Field(default=None, ge=1)
    animal_method: Literal["missile", "fishing"] | None = None
    terrain_modifier: int = Field(default=0, ge=-30, le=30)
    ration_definition_id: str | None = None
    supply_owner_id: str | None = None
    party_ht: tuple[tuple[str, int], ...] = ()
    meal_item_ids: tuple[str, ...] = ()
    water_item_ids: tuple[str, ...] = ()
    water_quarts_required: Literal[2, 3, 5] = 2
    recovery_meals_consumed: int = Field(default=0, ge=0, le=3)
    recovery_water_consumed: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def validate_task(self) -> SurvivalTask:
        if self.due <= self.start:
            raise ValueError("Survival activity must have positive duration")
        if self.status == "interrupted" and (
            self.interrupted_at is None or not self.start <= self.interrupted_at < self.due
        ):
            raise ValueError("Interrupted survival activity requires its interruption time")
        if self.kind == "forage" and (
            self.forage_mode is None
            or (self.plant_skill is None and self.animal_skill is None)
            or self.ration_definition_id is None
            or self.supply_owner_id is None
            or not self.party_ht
        ):
            raise ValueError("Foraging requires a complete authored skill and supply snapshot")
        if self.kind == "sleep" and any(
            value is not None
            for value in (
                self.forage_mode,
                self.plant_skill,
                self.animal_skill,
                self.animal_method,
                self.ration_definition_id,
                self.supply_owner_id,
            )
        ):
            raise ValueError("Sleep cannot carry foraging parameters")
        if self.animal_skill is not None and self.animal_method is None:
            raise ValueError("Animal foraging requires its authored method")
        if len({actor for actor, _ in self.party_ht}) != len(self.party_ht):
            raise ValueError("Foraging party actors must be unique")
        return self


def require_survival_settled(
    statuses: tuple[SurvivalStatus, ...],
    tasks: tuple[SurvivalTask, ...],
    actor_ids: frozenset[str],
    at: int,
) -> None:
    if any(s.actor_id in actor_ids and s.next_due <= at for s in statuses):
        raise ConflictError("Settle due survival needs before further activity")
    if any(
        t.actor_id in actor_ids and not t.settled and t.status == "pending" and t.due <= at
        for t in tasks
    ):
        raise ConflictError("Settle the due survival activity before continuing")
    if any(s.actor_id in actor_ids and s.forced_asleep for s in statuses):
        raise ValidationError("A sleeping actor cannot take voluntary activity")


def interrupt_survival_tasks(
    tasks: tuple[SurvivalTask, ...], actor_ids: frozenset[str], at: int
) -> tuple[SurvivalTask, ...]:
    return tuple(
        task.model_copy(update={"status": "interrupted", "interrupted_at": at})
        if task.actor_id in actor_ids and task.status == "pending" and at < task.due
        else task
        for task in tasks
    )


def validate_survival_records(
    statuses: tuple[SurvivalStatus, ...], tasks: tuple[SurvivalTask, ...], at: int
) -> None:
    if len({s.actor_id for s in statuses}) != len(statuses):
        raise ValueError("Duplicate survival actor")
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("Duplicate survival task ID")
    if any(s.started > at for s in statuses):
        raise ValueError("Survival schedule starts in the future")
    if any(t.start > at for t in tasks):
        raise ValueError("Survival activity starts in the future")
    if any(t.status == "completed" and (not t.settled or t.due > at) for t in tasks):
        raise ValueError("Completed survival activity must be due and settled")
