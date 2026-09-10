"""Opt-in B407 conventional firearm facts and durable failure state."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.models import Record


class FirearmSpec(Record):
    technology_level: int = Field(ge=3, le=12)
    action: Literal[
        "repeating", "revolver", "muzzleloader", "breechloader", "beam", "single-use", "grenade"
    ]
    fuse_seconds: int | None = Field(default=None, ge=1, le=60, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def construction(self) -> FirearmSpec:
        if self.action == "grenade" and self.fuse_seconds is None:
            raise ValueError("Grenade requires its authored normal fuse duration")
        if self.action != "grenade" and self.fuse_seconds is not None:
            raise ValueError("Only grenades have a fuse duration")
        if self.action == "beam" and self.technology_level < 6:
            raise ValueError("Beam construction requires TL6 or later")
        if self.action == "revolver" and self.technology_level < 5:
            raise ValueError("Revolver construction requires TL5 or later")
        return self

    quality: Literal["cheap", "ordinary", "fine", "very-fine"] = "ordinary"
    malfunction_override: int | None = Field(default=None, ge=3, le=19)
    armoury_skill_id: str | None = Field(default=None, min_length=1)

    @property
    def malfunction_number(self) -> int:
        if self.malfunction_override is not None:
            return self.malfunction_override
        return {3: 12, 4: 14, 5: 16}.get(self.technology_level, 17) + {
            "cheap": -1,
            "ordinary": 0,
            "fine": 1,
            "very-fine": 1,
        }[self.quality]


class FirearmFailure(Record):
    mode_id: str = Field(min_length=1)
    cause_id: str = Field(min_length=1)
    kind: Literal["misfire", "stoppage", "mechanical", "destroyed", "dud", "delayed", "explosion"]
    diagnosed: bool = False
    # A misfire retains its unfired round until clearing/ejection or cylinder advance.
    blocked_round: bool = False
    progress: int = Field(default=0, ge=0, le=3599)
    service_actor_id: str | None = None
    service_kind: Literal["clear", "repair"] | None = None
    service_skill: Literal["weapon", "armoury"] | None = None
