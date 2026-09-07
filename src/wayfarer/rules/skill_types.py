"""Typed, immutable skill mechanics stored inside a package's content digest."""

from dataclasses import dataclass
from enum import StrEnum


class Difficulty(StrEnum):
    EASY = "easy"
    AVERAGE = "average"
    HARD = "hard"
    VERY_HARD = "very-hard"


class ControllingAttribute(StrEnum):
    ST = "attribute:st"
    DX = "attribute:dx"
    IQ = "attribute:iq"
    HT = "attribute:ht"
    WILL = "secondary:will"
    PER = "secondary:per"


@dataclass(frozen=True, slots=True)
class SkillDefault:
    target: str
    modifier: int


@dataclass(frozen=True, slots=True)
class SkillPrerequisite:
    target: str
    minimum: int = 1


@dataclass(frozen=True, slots=True)
class Specialty:
    family: str
    name: str
    # Optional specialties refer to the unspecialized catalog skill. Required
    # specialties are distinct skills with no inferred cross-specialty defaults.
    optional_parent: str | None = None


@dataclass(frozen=True, slots=True)
class Technique:
    parent: str
    default_modifier: int
    maximum_modifier: int = 0


@dataclass(frozen=True, slots=True)
class SkillSpec:
    attribute: ControllingAttribute
    difficulty: Difficulty
    reference: str
    defaults: tuple[SkillDefault, ...] = ()
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    specialty: Specialty | None = None
    technique: Technique | None = None
