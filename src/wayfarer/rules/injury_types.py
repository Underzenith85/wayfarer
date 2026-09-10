"""Persisted injury facts; mechanics are in simulation.injury."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wayfarer.rules.location_types import InjuryTolerance, LastingInjury
from wayfarer.rules.physical_traits import NO_PHYSICAL_TRAITS, PhysicalTraits, SurpriseState


class InjuryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    profile_id: Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
    physical_traits: PhysicalTraits = Field(
        default=NO_PHYSICAL_TRAITS, exclude_if=lambda v: v == NO_PHYSICAL_TRAITS
    )
    surprise: SurpriseState | None = Field(default=None, exclude_if=lambda v: v is None)
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
    anatomy: Literal["human"] | None = None
    male_groin: bool = False
    tolerance: InjuryTolerance | None = Field(default=None, exclude_if=lambda v: v is None)
    lasting_injuries: tuple[LastingInjury, ...] = ()

    @model_validator(mode="after")
    def anatomy_consistent(self) -> Self:
        if (
            self.physical_traits != NO_PHYSICAL_TRAITS
            and self.profile_id != "gurps-basic-set-4e-2004"
        ):
            raise ValueError("Physical traits require their exact Basic Set profile")
        if self.tolerance is not None and (
            self.anatomy != "human" or self.profile_id != "gurps-basic-set-4e-2004"
        ):
            raise ValueError("Injury Tolerance requires explicit Basic Set anatomy")
        if self.lasting_injuries and (
            self.anatomy != "human" or self.profile_id != "gurps-basic-set-4e-2004"
        ):
            raise ValueError("Lasting locations require explicit Basic Set human anatomy")
        if self.male_groin and self.anatomy != "human":
            raise ValueError("Groin sensitivity requires explicit human anatomy")
        if len({w.id for w in self.lasting_injuries}) != len(self.lasting_injuries):
            raise ValueError("Duplicate lasting injury ID")
        return self

    @property
    def incapacitated(self) -> bool:
        return self.unconscious or self.mortal_wound or self.dead
