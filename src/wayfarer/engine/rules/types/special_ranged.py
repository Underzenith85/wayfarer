"""Typed B407-B413 cover and in-flight guidance records."""

from decimal import Decimal
from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.models import Id, Record


class GuidanceSpec(Record):
    """Explicit equipment adapter for a steerable projectile (B412-B413)."""

    kind: Literal["guided", "homing", "semi-active"]
    seeker_skill: int = Field(default=10, ge=3, le=30)
    seeker_sense: Id | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def complete_adapter(self) -> Self:
        if self.kind == "guided" and self.seeker_sense is not None:
            raise ValueError("Guided weapons use the operator's senses")
        if self.kind != "guided" and self.seeker_sense is None:
            raise ValueError("Homing weapons require an explicit seeker sense")
        return self


class GuidanceState(Record):
    """Durable lock/flight state; all distances are authoritative map distances."""

    id: Id
    weapon_id: Id
    mode_id: Id
    operator_id: Id
    target_id: Id
    kind: Literal["guided", "homing", "semi-active"]
    status: Literal["locked", "in-flight", "lost", "arrived"] = "locked"
    speed_yards_per_second: Decimal = Field(gt=0, allow_inf_nan=False)
    remaining_yards: Decimal = Field(ge=0, allow_inf_nan=False)
    remaining_endurance_yards: Decimal = Field(ge=0, allow_inf_nan=False)
    accuracy: int = Field(ge=0)
    lock: CheckTrace
    designator_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    lost_reason: (
        Literal[
            "operator-interrupted",
            "target-lost",
            "designation-lost",
            "jammed",
            "out-of-range",
        ]
        | None
    ) = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def state_consistency(self) -> Self:
        if self.kind == "semi-active" and self.designator_id is None:
            raise ValueError("Semi-active homing requires a recorded designator")
        if self.status == "lost" and self.lost_reason is None:
            raise ValueError("Lost guidance requires a reason")
        if self.status != "lost" and self.lost_reason is not None:
            raise ValueError("Only lost guidance may retain a loss reason")
        if self.status == "arrived" and self.remaining_yards != 0:
            raise ValueError("Arrived guidance cannot retain distance")
        return self


class CoverImpact(Record):
    """Receipt detail for one projectile crossing one intervening object."""

    projectile_id: Id
    barrier_item_id: Id
    target_id: Id
    basic_damage: int = Field(ge=0)
    cover_dr: int = Field(ge=0)
    residual_damage: int = Field(ge=0)
    object_command_id: Id
