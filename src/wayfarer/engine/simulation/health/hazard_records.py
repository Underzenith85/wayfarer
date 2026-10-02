"""Stable command and result records for canonical environmental hazards."""

from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Record


class HazardCommand(Command):
    kind: Literal["enter", "resolve", "leave"]
    hazard_id: str


class HazardResult(Record):
    schedule_id: str
    active: bool
    due: int
    hp_lost: int = 0
    fp_lost: int = 0
    check: CheckTrace | None = None
    consciousness: CheckTrace | None = None
    conditions: tuple[str, ...] = ()
    radiation_dose: int = 0
