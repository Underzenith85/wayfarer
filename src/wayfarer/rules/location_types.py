"""Explicit living-human anatomy and durable impairments (B398-400, B420-422)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

HumanLocation = Literal[
    "torso",
    "vitals",
    "skull",
    "face",
    "neck",
    "groin",
    "left-arm",
    "right-arm",
    "left-leg",
    "right-leg",
    "left-hand",
    "right-hand",
    "left-foot",
    "right-foot",
    "left-eye",
    "right-eye",
]
HitLocation = HumanLocation | Literal["random"]
Hand = Literal["left-hand", "right-hand"]


class InjuryTolerance(BaseModel):
    """Trusted anatomy facts; never accepted as player-authored damage modifiers."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    structure: Literal["living", "unliving", "homogenous", "diffuse"] = "living"
    no_brain: bool = False
    no_eyes: bool = False
    no_head: bool = False
    no_neck: bool = False
    no_vitals: bool = False


class HumanBody(BaseModel):
    """Trusted scenario anatomy; absence never selects a human implicitly."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    anatomy: Literal["human"]
    male_groin: bool = False
    tolerance: InjuryTolerance | None = Field(default=None, exclude_if=lambda v: v is None)


class LastingInjury(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(min_length=1)
    location: HumanLocation
    kind: Literal["crippled", "destroyed", "severed", "disabled"]
    duration: Literal["pending", "temporary", "lasting", "permanent", "timed"]
    inflicted_at: int = Field(ge=0)
    injury: int = Field(ge=0)
    recovery_at: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def coherent_duration(self) -> Self:
        if (self.duration in ("lasting", "timed")) != (self.recovery_at is not None):
            raise ValueError("Lasting and timed injuries require a recovery deadline")
        if self.recovery_at is not None and self.recovery_at <= self.inflicted_at:
            raise ValueError("Recovery follows injury")
        if self.kind in ("severed", "destroyed") and self.duration != "permanent":
            raise ValueError("Destroyed body parts require explicit permanent recovery")
        return self

    def active(self, *, now: int, full_hp: bool) -> bool:
        if self.duration == "temporary":
            return not full_hp
        if self.duration in ("lasting", "timed") and self.recovery_at is not None:
            return now < self.recovery_at
        return True


def disabled_locations(
    injuries: tuple[LastingInjury, ...], *, now: int, full_hp: bool
) -> frozenset[HumanLocation]:
    return frozenset(i.location for i in injuries if i.active(now=now, full_hp=full_hp))
