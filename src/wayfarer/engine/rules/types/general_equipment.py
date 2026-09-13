"""Typed B288-289 general-equipment and accessory capabilities."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class EquipmentQuality(Record):
    """B345 quality/time-independent equipment modifier and price multiple."""

    grade: Literal["none", "improvised", "basic", "good", "fine", "best"] = "basic"
    technological_task: bool = False
    missing_important_items: int = Field(default=0, ge=0)
    damage_penalty: int = Field(default=0, ge=0, le=3)
    technology_level: int = Field(default=0, ge=0)

    @property
    def modifier(self) -> int:
        quality = {
            "none": -10 if self.technological_task else -5,
            "improvised": -5 if self.technological_task else -2,
            "basic": 0,
            "good": 1,
            "fine": 2,
            "best": max(2, self.technology_level // 2),
        }[self.grade]
        return quality - self.missing_important_items - self.damage_penalty

    @property
    def cost_multiplier(self) -> int:
        return {"none": 0, "improvised": 0, "basic": 1, "good": 5, "fine": 20, "best": 20}[
            self.grade
        ]


class GeneralEquipmentFeature(Record):
    """One source-defined behavior; fields are interpreted only by its kind."""

    kind: Literal[
        "accessory",
        "breathing",
        "communication",
        "container",
        "fuel",
        "light",
        "medical",
        "mount",
        "protection",
        "recording",
        "restraint",
        "sensor",
        "support",
        "tool",
    ]
    skill_id: Id | None = None
    modifier: int = Field(default=0, ge=-10, le=10)
    capacity: int | None = Field(default=None, ge=1)
    duration_seconds: int | None = Field(default=None, ge=1)
    range_yards: int | None = Field(default=None, ge=1)
    consumable_definition_id: Id | None = None
    consumable_units: int = Field(default=0, ge=0)
    target: Literal["actor", "weapon", "mount", "environment"] = "actor"
    trait_id: Id | None = None
    grants_holdout: bool = False
    note: str = Field(default="", max_length=300)
    technology_relative: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.consumable_definition_id is None) != (self.consumable_units == 0):
            raise ValueError("Consumable identity and units must be authored together")
        if self.kind == "accessory" and self.target not in ("actor", "weapon"):
            raise ValueError("Accessories attach to an actor or weapon")
        if self.kind == "tool" and self.skill_id is None:
            raise ValueError("Tool capability requires an exact skill")
        return self
