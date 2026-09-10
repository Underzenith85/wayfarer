"""Pinned physical trait projection, Characters 4e third printing B35-79.

These values are derived from approved purchases, never from player commands.
They do not alter attributes or HT-based skills. Zero values preserve legacy
profiles and serialized state. Rules services consume this projection directly.
"""

from typing import Literal

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Record

Sense = Literal["hearing", "taste-smell", "touch", "vision"]


class PhysicalTraits(Record):
    ambidexterity: bool = False
    combat_reflexes: bool = False
    fitness: int = Field(default=0, ge=0, le=2)
    high_pain_threshold: bool = False
    night_vision: int = Field(default=0, ge=0, le=9)
    acute_hearing: int = Field(default=0, ge=0, le=10)
    acute_taste_smell: int = Field(default=0, ge=0, le=10)
    acute_touch: int = Field(default=0, ge=0, le=10)
    acute_vision: int = Field(default=0, ge=0, le=10)
    healing: int = Field(default=0, ge=0, le=2)

    def darkness(self, penalty: int) -> int:
        if type(penalty) is not int or not -10 <= penalty <= 0:
            raise ValidationError("Darkness must be within -10..0")
        return penalty if penalty == -10 else min(0, penalty + self.night_vision)

    def sense_bonus(self, sense: Sense, darkness: int = 0) -> int:
        if sense not in ("hearing", "taste-smell", "touch", "vision"):
            raise ValidationError("Unsupported sense")
        return int(getattr(self, "acute_" + sense.replace("-", "_"))) + (
            self.darkness(darkness) if sense == "vision" else 0
        )

    def injury_bonus(self, reason: str, *, mental: bool = False) -> int:
        if mental:
            return 6 if self.combat_reflexes else 0
        return (
            self.fitness
            + (3 if self.high_pain_threshold and reason == "major-wound" else 0)
            + (5 if self.healing and reason == "crippling-duration" else 0)
        )


NO_PHYSICAL_TRAITS = PhysicalTraits()
PHYSICAL_BINDINGS: dict[str, tuple[str, str, int | bool]] = {
    "trait:ambidexterity": ("trait.off_hand", "ambidexterity", True),
    "trait:combat-reflexes": ("trait.combat_reflexes", "combat_reflexes", True),
    "trait:fit": ("trait.fitness", "fitness", 1),
    "trait:very-fit": ("trait.fitness", "fitness", 2),
    "trait:high-pain-threshold": ("trait.pain", "high_pain_threshold", True),
    "trait:night-vision": ("trait.darkness", "night_vision", 1),
    "trait:rapid-healing": ("trait.healing", "healing", 1),
    "trait:very-rapid-healing": ("trait.healing", "healing", 2),
    **{
        f"trait:acute-{sense}": ("trait.senses", "acute_" + sense.replace("-", "_"), 1)
        for sense in ("hearing", "taste-smell", "touch", "vision")
    },
}
PHYSICAL_HOOKS = frozenset(value[0] for value in PHYSICAL_BINDINGS.values())


class SurpriseState(Record):
    partial: bool
    freeze_turns: int = Field(default=0, ge=0, le=6)
    attempts: int = Field(default=0, ge=0)
