"""Interval requests and receipts shared by physiology reducers and schedules."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.resources import Command
from wayfarer.engine.simulation.traits.physiology_calendar import PhysiologyCalendar
from wayfarer.models import Record


class PhysiologyInterval(Record):
    id: str
    actor_id: str
    kind: Literal["regeneration", "dependency", "weakness", "extra-life"]
    due: int = Field(ge=0)
    amount: int = Field(default=1, ge=1, le=1000)
    active: bool = True
    calendar: PhysiologyCalendar | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    started: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    dependency_due: int | None = Field(default=None, ge=0, exclude_if=lambda value: value is None)


class PhysiologyCommand(Command):
    interval_id: str


class PhysiologyOutcome(Record):
    actor_id: str
    kind: Literal["regenerated", "injured", "revived", "unavailable"]
    hp_before: int
    hp_after: int
    interval_id: str
    injury: InjuryResult | None = None
    damage_dice: tuple[int, ...] = ()


class PhysiologyEvent(Record):
    command_id: str
    outcome: PhysiologyOutcome
    interval: PhysiologyInterval | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    request_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)
