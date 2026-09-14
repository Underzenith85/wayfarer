"""Authored B425 ultra-tech drug construction math."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class UltraTechDrug(Record):
    id: Id
    absolute_point_value: int = Field(gt=0)
    duration: Literal["short", "medium", "long", "very-long"]
    potency_penalty: int = Field(default=0, le=0)
    delivery: Literal["dose", "aerosol", "contact", "aerosol-contact"] = "dose"
    form: Literal["pill", "injection", "inhaled", "contact"] = "pill"
    legality_class: int = Field(default=3, ge=0, le=4)
    very_long_seconds: int | None = Field(default=None, ge=86_400, le=7 * 86_400)

    @model_validator(mode="after")
    def exact_duration(self) -> UltraTechDrug:
        if (self.duration == "very-long") != (self.very_long_seconds is not None):
            raise ValueError("Very-long drugs require an authored duration up to one week")
        return self

    def duration_seconds(self, *, ht: int) -> int:
        if ht < 1:
            raise ValueError("HT must be positive")
        return {
            "short": max(1, 25 - ht) * 60,
            "medium": 6 * 3600,
            "long": 86_400,
            "very-long": self.very_long_seconds or 0,
        }[self.duration]

    @property
    def resistance_modifier(self) -> int:
        return self.potency_penalty

    @property
    def cost(self) -> int:
        duration_cost = {"short": 2, "medium": 10, "long": 50, "very-long": 250}[self.duration]
        potency = 2 ** abs(self.potency_penalty)
        delivery = {"dose": 1, "aerosol": 2, "contact": 2, "aerosol-contact": 10}[self.delivery]
        return int(self.absolute_point_value * duration_cost * potency * delivery)

    @staticmethod
    def dose_resistance_modifier(doses: int) -> int:
        """Each doubling of an otherwise identical dose gives -1 to resist."""
        if doses < 1:
            raise ValueError("At least one dose is required")
        return -(doses.bit_length() - 1)
