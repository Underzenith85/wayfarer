"""Persisted injury facts; mechanics are in simulation.injury."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InjuryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    profile_id: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
    shock: int = Field(default=0, ge=0, le=8)
    stunned: bool = False
    prone: bool = False
    unconscious: bool = False
    mortal_wound: bool = False
    mortal_wound_due: int | None = Field(default=None, ge=0)
    mortal_wound_started: int = Field(default=0, ge=0)
    dead: bool = False
    turn: int = Field(default=0, ge=0)
    phase: Literal["between", "acting"] = "between"
    shock_expires: int = Field(default=0, ge=0)

    @property
    def incapacitated(self) -> bool:
        return self.unconscious or self.mortal_wound or self.dead
