"""Typed B288-B289 general-equipment and accessory procedures."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record

Positive = Annotated[int, Field(ge=1)]
Nonnegative = Annotated[int, Field(ge=0)]


class EquipmentUseSpec(Record):
    """One source-defined way an ordinary item changes a task or situation."""

    id: Id
    kind: Literal[
        "basic-equipment",
        "skill-bonus",
        "sense-bonus",
        "control-bonus",
        "capacity",
        "protection",
        "rest",
        "light",
        "fire",
        "purify",
        "record",
        "restraint",
        "mobility",
        "production",
        "maintenance",
        "communication",
    ]
    skill_ids: tuple[Id, ...] = ()
    modifier: int = 0
    duration_seconds: Positive | None = None
    capacity: Positive | None = None
    range_yards: Positive | None = None
    protection: Positive | None = None
    consumes_definition_id: Id | None = None
    consumes_quantity: Positive = 1
    minimum_technology_level: Nonnegative | None = None

    @model_validator(mode="after")
    def meaningful(self) -> Self:
        if self.kind in ("basic-equipment", "skill-bonus") and not self.skill_ids:
            raise ValueError("Task equipment requires an exact skill family")
        if self.kind == "skill-bonus" and not self.modifier:
            raise ValueError("Skill bonus equipment requires a nonzero modifier")
        if self.kind == "capacity" and self.capacity is None:
            raise ValueError("Capacity equipment requires an exact capacity")
        return self


class FuelSpec(Record):
    """A supply or appliance duration; charges are authoritative item state."""

    kind: Literal["supply", "appliance"]
    fuel_id: Id | None = None
    seconds_per_charge: Positive
    initial_charges: Positive = 1

    @model_validator(mode="after")
    def appliance_requires_fuel(self) -> Self:
        if (self.kind == "appliance") != (self.fuel_id is not None):
            raise ValueError("Only an appliance names its compatible fuel")
        return self


class AccessorySpec(Record):
    """Attachment compatibility and the exact B289/B412 consequence."""

    kind: Literal[
        "hearing-protection",
        "quiver",
        "holster",
        "lanyard",
        "laser-sight",
        "scope",
        "silencer",
        "load-bearing",
    ]
    compatible: Literal["actor", "bow", "pistol", "pistol-or-smg", "ranged-weapon"]
    capacity: Nonnegative = 0
    attack_bonus: int = 0
    dodge_bonus_to_visible_target: int = 0
    accuracy_bonus: int = 0
    minimum_aim_seconds: Nonnegative = 0
    hearing_modifier: int = 0
    holdout_modifier: int = 0
    fast_draw_modifier: int = 0
    retrieval_ready_seconds: Positive = 1
    cut_dr: Nonnegative = 0
    cut_hp: Nonnegative = 0
    grants_infravision: bool = False
    powered_seconds: Positive | None = None

    @model_validator(mode="after")
    def exact_effect(self) -> Self:
        if self.kind == "scope" and (self.accuracy_bonus < 1 or self.minimum_aim_seconds < 1):
            raise ValueError("A scope requires its Acc bonus and Aim time")
        if self.kind == "laser-sight" and (self.attack_bonus != 1 or self.powered_seconds is None):
            raise ValueError("A laser sight requires its powered +1 attack procedure")
        if self.kind in ("quiver", "load-bearing") and self.capacity < 1:
            raise ValueError("Carrying accessories require a capacity")
        return self


class UltraTechDrugSpec(Record):
    """Authored TL9+ drug-design inputs from Campaigns B425."""

    id: Id
    technology_level: Annotated[int, Field(ge=9)] = 9
    trait_point_values: tuple[int, ...] = Field(min_length=1)
    duration: Literal["short", "medium", "long", "very-long"]
    potency: Nonnegative = 0
    form: Literal["pill", "injection", "aerosol", "contact", "aerosol-contact"]
    legality_class: Annotated[int, Field(ge=0, le=4)] = 3
    healing: Literal["none", "hp", "fp"] = "none"

    @property
    def base_duration_seconds(self) -> int | None:
        return {"short": None, "medium": None, "long": 86_400, "very-long": 604_800}[self.duration]

    def duration_seconds(self, health: int) -> int:
        if health < 1:
            raise ValueError("Drug duration requires positive HT")
        remaining = max(0, 25 - health)
        if self.duration == "short":
            return remaining * 60
        if self.duration == "medium":
            return remaining * 15 * 60
        return self.base_duration_seconds or 0

    def cost(self) -> Decimal:
        base = {"short": 2, "medium": 10, "long": 50, "very-long": 250}[self.duration]
        form = {"pill": 1, "injection": 1, "aerosol": 2, "contact": 2, "aerosol-contact": 10}[
            self.form
        ]
        return Decimal(
            sum(abs(value) for value in self.trait_point_values) * base * 2**self.potency * form
        )
