"""Explicit ground transport checkpoint schema; Campaigns fourth printing B396-397, B469."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Transport(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    source_id: Literal["gurps-basic-set-campaigns-4e-fourth-printing"] = (
        "gurps-basic-set-campaigns-4e-fourth-printing"
    )
    control_dice: tuple[int, int, int] | None = None
    control_target: int | None = None
    control_margin: int | None = None
    id: str = Field(min_length=1, max_length=200)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    locomotion: Literal["ground-wheeled", "ground-mount"]
    body_id: str = Field(min_length=1, max_length=200)
    operator_id: str = Field(min_length=1, max_length=200)
    occupants: tuple[str, ...] = Field(min_length=1)
    acceleration: int = Field(gt=0, le=100)
    top_speed: int = Field(gt=0, le=100)
    handling: int = Field(default=0, ge=-10, le=10)
    stability: int = Field(default=1, ge=1, le=10)
    speed: int = Field(default=0, ge=0, le=100)
    q: int = Field(default=0, ge=-1000, le=1000)
    r: int = Field(default=0, ge=-1000, le=1000)
    facing: Literal[0, 1, 2, 3, 4, 5] = 0
    # Authored longitudinal footprint, with the reference hex at offset zero.
    footprint: tuple[int, ...] = (0,)
    status: Literal["controlled", "skidding", "crashed", "spooked", "lost"] = "controlled"
    last_turn: int = Field(default=-1, ge=-1)
    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)
    attack_penalty: int = Field(default=0, le=0)
    aim_lost: bool = False
    restraints: Literal["none", "seatbelts", "airbags"] = "none"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            len(set(self.occupants)) != len(self.occupants)
            or self.operator_id not in self.occupants
        ):
            raise ValueError("Transport requires a unique manifest including its operator")
        if self.speed > self.top_speed:
            raise ValueError("Transport exceeds top speed")
        if (
            not self.footprint
            or 0 not in self.footprint
            or len(set(self.footprint)) != len(self.footprint)
        ):
            raise ValueError("Transport requires a unique footprint including its reference hex")
        if sorted(self.footprint) != list(range(min(self.footprint), max(self.footprint) + 1)):
            raise ValueError("Transport footprint must be contiguous")
        if self.locomotion == "ground-mount" and (
            self.occupants != (self.operator_id,) or self.restraints != "none"
        ):
            raise ValueError("Mount slice supports one unrestrained rider")
        return self
