"""Explicit opt-in B426 temperate ordinary-battle policy, included in package pins."""

from dataclasses import dataclass
from typing import Literal

from wayfarer.errors import ValidationError


@dataclass(frozen=True, slots=True)
class BattleFatiguePolicy:
    generation: Literal[1] = 1
    maximum_seconds: int = 119
    climate: Literal["temperate"] = "temperate"

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation != 1:
            raise ValidationError("Unsupported battle fatigue generation")
        if type(self.maximum_seconds) is not int or not 11 <= self.maximum_seconds <= 119:
            raise ValidationError("Battle fatigue duration requires a bounded ordinary battle")
        if self.climate != "temperate":
            raise ValidationError("Battle fatigue requires authored temperate conditions")
