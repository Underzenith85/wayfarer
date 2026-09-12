"""Explicit living-human anatomy and durable impairments (B398-400, B420-422)."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record

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


class InjuryTolerance(Record):
    """Trusted anatomy facts; never accepted as player-authored damage modifiers."""

    structure: Literal["living", "unliving", "homogenous", "diffuse"] = "living"
    no_brain: bool = False
    no_eyes: bool = False
    no_head: bool = False
    no_neck: bool = False
    no_vitals: bool = False


class HumanBody(Record):
    """Trusted scenario anatomy; absence never selects a human implicitly."""

    anatomy: Literal["human"]
    male_groin: bool = False
    tolerance: InjuryTolerance | None = Field(default=None, exclude_if=lambda v: v is None)


class LastingInjury(Record):
    id: str = Field(min_length=1)
    location: HumanLocation
    kind: Literal["crippled", "destroyed", "severed", "disabled", "deafened", "scarred"]
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
        if self.kind in ("severed", "destroyed", "scarred") and self.duration != "permanent":
            raise ValueError("Destroyed body parts and scars require a permanent duration")
        if self.kind == "scarred" and self.injury not in (1, 2):
            raise ValueError("Severe scarring records one or two lost appearance levels")
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
    return frozenset(
        i.location
        for i in injuries
        if i.kind in ("crippled", "destroyed", "severed", "disabled")
        and i.active(now=now, full_hp=full_hp)
    )


def deafened(injuries: tuple[LastingInjury, ...], *, now: int, full_hp: bool) -> bool:
    """Whether an active critical-head consequence prevents ordinary hearing."""
    return any(i.kind == "deafened" and i.active(now=now, full_hp=full_hp) for i in injuries)


def appearance_levels_lost(injuries: tuple[LastingInjury, ...], *, now: int, full_hp: bool) -> int:
    """Durable appearance loss available to social-reaction consumers."""
    return sum(
        i.injury for i in injuries if i.kind == "scarred" and i.active(now=now, full_hp=full_hp)
    )
