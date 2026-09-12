"""What a caller asks for: begin or finish one treatment, and what it reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.recovery import ProfileId
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Record


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
