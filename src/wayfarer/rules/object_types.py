"""Opt-in, persisted nonsentient object facts (Basic Set B380, B483-484)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    construction: Literal["unliving", "homogenous"]
    hp: int = Field(gt=0)
    dr: int = Field(ge=0)
    ht: int = Field(gt=0)


class ObjectCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    hp: int
    disabled: bool = False
    destroyed: bool = False
    last_stress_at: int | None = Field(default=None, ge=0)

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
