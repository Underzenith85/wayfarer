"""Opt-in, persisted nonsentient object facts (Basic Set B380, B483-484)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    construction: Literal["unliving", "homogenous", "diffuse"]
    hp: int = Field(gt=0)
    dr: int = Field(ge=0)
    ht: int = Field(gt=0)
    quality: Literal["cheap", "average", "fine", "very-fine"] = Field(
        default="average", exclude_if=lambda v: v == "average"
    )
    break_resistant: bool = Field(default=False, exclude_if=lambda v: not v)
    high_pain_threshold: bool = Field(default=False, exclude_if=lambda v: not v)
    size_modifier: int | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_skill_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_tools_definition: str | None = Field(default=None, exclude_if=lambda v: v is None)
    repair_parts_definition: str | None = Field(default=None, exclude_if=lambda v: v is None)
    # Six reviewed B485 outcomes, indexed by the recorded d6. None means unusable.
    residual_definitions: tuple[str | None, ...] = Field(default=(), exclude_if=lambda v: not v)

    @model_validator(mode="after")
    def residual_table(self) -> Self:
        if self.residual_definitions and len(self.residual_definitions) != 6:
            raise ValueError("Broken weapon outcomes require exactly six entries")
        return self


class GroundPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    encounter_id: str = Field(min_length=1, max_length=200)
    geometry: Literal["grid", "hex"]
    x: int
    y: int


class ObjectCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    hp: int
    disabled: bool = False
    destroyed: bool = False
    last_stress_at: int | None = Field(default=None, ge=0)
    residual_roll: int | None = Field(default=None, ge=1, le=6, exclude_if=lambda v: v is None)
    shock: int = Field(default=0, ge=0, le=4, exclude_if=lambda v: not v)
    shock_until: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def destruction_disables(self) -> Self:
        if self.destroyed and not self.disabled:
            raise ValueError("Destroyed objects must be disabled")
        return self


class ObjectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    command_id: str = Field(min_length=1, max_length=200)
    item_id: str = Field(min_length=1, max_length=200)
    injury: int = Field(default=0, ge=0)
    effective_dr: int = Field(default=0, ge=0)
    checks: tuple[tuple[int, int, int], ...] = ()
    condition: ObjectCondition


def residual_definition(
    profile: ObjectProfile | None, condition: ObjectCondition | None
) -> str | None:
    if (
        profile is None
        or condition is None
        or not condition.disabled
        or condition.destroyed
        or condition.residual_roll is None
        or not profile.residual_definitions
    ):
        return None
    return profile.residual_definitions[condition.residual_roll - 1]
