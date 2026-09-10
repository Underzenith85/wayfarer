"""Opt-in Basic projectile readiness; authored unloading is not source certification."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectileReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["bow", "crossbow", "projectile", "firearm"]
    fast_draw_skill_id: str | None = None
    fast_draw_specialty: Literal["Arrow", "Ammo"] | None = None
    fast_draw_seconds: int = Field(default=1, ge=1)
    cocking_aid_definition_id: str | None = None
    unload_seconds_per_round: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.fast_draw_skill_id is None) != (self.fast_draw_specialty is None):
            raise ValueError("Fast-Draw requires an exact skill and specialty")
        if self.fast_draw_specialty and self.fast_draw_specialty != (
            "Ammo" if self.kind == "firearm" else "Arrow"
        ):
            raise ValueError("Fast-Draw specialty does not match readiness protocol")
        if self.kind != "firearm" and self.fast_draw_seconds != 1:
            raise ValueError("Fast-Draw (Arrow) saves exactly one Ready")
        if self.cocking_aid_definition_id and self.kind != "crossbow":
            raise ValueError("Cocking aids require crossbow readiness")
        return self


class ProjectileProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    stage: Literal["prepare", "draw", "cock", "load", "loaded", "unload"] = "prepare"
    elapsed: int = Field(default=0, ge=0)
    required: int = Field(default=0, ge=0)
    fast_draw_used: bool = False
    cocking_aid_id: str | None = None
