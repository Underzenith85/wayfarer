"""Opt-in B407 conventional firearm facts and durable failure state."""

from typing import Literal

from pydantic import Field

from wayfarer.models import Record


class FirearmSpec(Record):
    # TL3/4 explosions, beams, grenades and explosive ammunition need distinct protocols.
    technology_level: int = Field(ge=5, le=12)
    action: Literal["repeating", "revolver"]
    quality: Literal["cheap", "ordinary", "fine", "very-fine"] = "ordinary"
    malfunction_override: int | None = Field(default=None, ge=3, le=19)
    armoury_skill_id: str | None = Field(default=None, min_length=1)

    @property
    def malfunction_number(self) -> int:
        if self.malfunction_override is not None:
            return self.malfunction_override
        return (16 if self.technology_level == 5 else 17) + {
            "cheap": -1,
            "ordinary": 0,
            "fine": 1,
            "very-fine": 1,
        }[self.quality]


class FirearmFailure(Record):
    mode_id: str = Field(min_length=1)
    cause_id: str = Field(min_length=1)
    kind: Literal["misfire", "stoppage", "mechanical", "destroyed"]
    diagnosed: bool = False
    # A misfire retains its unfired round until clearing/ejection or cylinder advance.
    blocked_round: bool = False
    progress: int = Field(default=0, ge=0, le=3599)
    service_actor_id: str | None = None
    service_kind: Literal["clear", "repair"] | None = None
    service_skill: Literal["weapon", "armoury"] | None = None
