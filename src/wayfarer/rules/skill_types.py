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


class DefaultConditionKind(StrEnum):
    """Authoritative facts that may gate a B168-173 skill default."""

    MATCHING_TECHNOLOGY_LEVEL = "matching-technology-level"
    MATCHING_SPECIALTY = "matching-specialty"
    REQUIRED_EQUIPMENT = "required-equipment"


class PrerequisiteKind(StrEnum):
    """The authoritative fact that satisfies a skill-acquisition requirement."""

    TRAINED_SKILL = "trained-skill"
    PURCHASED_DEFINITION = "purchased-definition"
    CAPABILITY = "capability"


@dataclass(frozen=True, slots=True)
class DefaultCondition:
    kind: DefaultConditionKind
    # Only ``required-equipment`` names a value: the pinned equipment definition.
    value: str | None = None


@dataclass(frozen=True, slots=True)
class SkillDefault:
    target: str
    modifier: int
    conditions: tuple[DefaultCondition, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillPrerequisite:
    target: str
    minimum: int = 1
    kind: PrerequisiteKind = PrerequisiteKind.TRAINED_SKILL
    # Some prerequisites only exist at or above a skill's TL (B190, B213, B217).
    minimum_technology_level: int | None = None


@dataclass(frozen=True, slots=True)
class Specialty:
    family: str
    name: str
    # Optional specialties refer to the unspecialized catalog skill. Required
    # specialties are distinct skills with no inferred cross-specialty defaults.
    optional_parent: str | None = None


@dataclass(frozen=True, slots=True)
class PrerequisiteGroup:
    """An alternative set: satisfying any one member satisfies the requirement.

    B168 states several prerequisites as "A or B". Flattening that into the
    ``prerequisites`` AND list would either demand both or silently drop one, so
    an alternative set is recorded as its own shape.
    """

    alternatives: tuple[SkillPrerequisite, ...]


@dataclass(frozen=True, slots=True)
class Technique:
    parent: str
    default_modifier: int
    maximum_modifier: int = 0


@dataclass(frozen=True, slots=True)
class TechniqueTemplate:
    """A B230-233 technique listing, before a concrete parent is chosen.

    A template is not rollable: the same technique bought against Judo and
    against Karate is two distinct skills. ``parents`` names the skills the
    source permits, and ``parent_family`` points at a variable family when the
    source permits a whole class ("any melee weapon skill") instead of a list.
    ``attribute`` is the parent's controlling attribute unless the source
    overrides it, as ST-based Neck Snap does.
    """

    difficulty: Difficulty
    default_modifier: int
    maximum_modifier: int = 0
    parents: tuple[str, ...] = ()
    parent_family: str | None = None
    attribute: ControllingAttribute | None = None

    def expand(self, parent: str) -> Technique:
        """The concrete parent-relative technique for one permitted parent."""
        if parent not in self.parents:
            raise ValueError(f"Parent is outside the template's permitted set: {parent}")
        return Technique(parent, self.default_modifier, self.maximum_modifier)


@dataclass(frozen=True, slots=True)
class VariableFamily:
    """A family whose specialties the player defines, so no list can enumerate them.

    Hobby Skill, Professional Skill, the Combat Art/Sport pair and the Melee
    Weapon class are open sets. Recording the shape is the whole of the contextual
    metadata; an enumeration would be an invention, not a reconciliation.
    """

    subject: str
    # How a chosen specialty gets its mechanics: mirroring the combat skill it is
    # an art of, or chosen with the subject. An open family may leave the
    # controlling attribute and difficulty to that choice, so neither is required.
    determination: str
    mirrors: str | None = None
    attribute: ControllingAttribute | None = None
    difficulty: Difficulty | None = None


@dataclass(frozen=True, slots=True)
class SkillSpec:
    attribute: ControllingAttribute
    difficulty: Difficulty
    reference: str
    defaults: tuple[SkillDefault, ...] = ()
    prerequisites: tuple[SkillPrerequisite, ...] = ()
    specialty: Specialty | None = None
    technique: Technique | None = None
    # One satisfied alternative per group, in addition to every ``prerequisites``
    # entry. An empty tuple keeps historic package digests byte-for-byte.
    prerequisite_groups: tuple[PrerequisiteGroup, ...] = ()
