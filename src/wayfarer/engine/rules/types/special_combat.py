"""Mounted and personal-flight combat facts (Campaigns B396-B398)."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class MountedCombatRelationship(Record):
    """One rider and one creature mount; neither identity is an equipment alias."""

    rider_id: Id
    mount_id: Id
    transport_id: Id
    riding_skill: int = Field(ge=1, le=50)
    saddle: bool = False
    stirrups: bool = False
    war_trained: bool
    control: Literal["controlled", "spooked", "lost", "separated"] = "controlled"

    @model_validator(mode="after")
    def distinct_combatants(self) -> Self:
        if self.rider_id == self.mount_id:
            raise ValueError("Rider and mount must remain separate combatants")
        if self.stirrups and not self.saddle:
            raise ValueError("Stirrups require a saddle")
        return self


class PersonalFlightState(Record):
    """Personal powered or winged flight, deliberately not a vehicle manifest."""

    source: Literal["personal-flight"] = "personal-flight"
    altitude: int = Field(ge=0, le=1000000)
    basic_air_move: int = Field(ge=1, le=100)
    top_air_speed: int = Field(ge=1, le=10000)
    velocity: int = Field(default=0, ge=0, le=10000)
    winged: bool = False
    cannot_hover: bool = False
    status: Literal["flying", "diving", "stalled", "falling"] = "flying"

    @model_validator(mode="after")
    def coherent_speed(self) -> Self:
        if self.basic_air_move > self.top_air_speed or self.velocity > self.top_air_speed * 2:
            raise ValueError("Personal-flight speed exceeds its authored envelope")
        if self.altitude == 0 and self.status != "flying":
            raise ValueError("A grounded combatant cannot retain an aerial failure state")
        return self


class FlightStep(Record):
    q: int = Field(ge=-1000, le=1000)
    r: int = Field(ge=-1000, le=1000)
    altitude: int = Field(ge=0, le=1000000)
