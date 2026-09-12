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
    BIOGRAPHICAL = "biographical"
    CAMPAIGN_SELECTED = "campaign-selected"
    MINIMUM_TECHNOLOGY_LEVEL = "minimum-technology-level"
    VESSEL = "vessel"
    ACTION_MODE = "action-mode"


class BiographicalDefault(StrEnum):
    """Character-history axes that can authorize a source default."""

    HOME_AREA = "home-area"
    NATIVE_PLANET_TYPE = "native-planet-type"
    NATIVE_CULTURE = "native-culture"


class DefaultActionMode(StrEnum):
    """Action modes whose presence changes the legal default alternatives."""

    ON_FOOT = "on-foot"
    DISARM_TRAP = "disarm-trap"
    RESET_TRAP = "reset-trap"


class DefaultVessel(StrEnum):
    """Vessel facts used by the conditional Shiphandling defaults on B220."""

    POWERED_SHIP = "powered-ship"
    TALL_SHIP = "tall-ship"


class PrerequisiteKind(StrEnum):
    """The authoritative fact that satisfies a skill-acquisition requirement."""

    TRAINED_SKILL = "trained-skill"
    PURCHASED_DEFINITION = "purchased-definition"
    CAPABILITY = "capability"


@dataclass(frozen=True, slots=True)
class DefaultCondition:
    kind: DefaultConditionKind
    # The condition kind determines whether this is an identifier, enum value,
    # or minimum TL. Campaign-selected and matching predicates name no value.
    value: str | int | None = None


@dataclass(frozen=True, slots=True)
class CampaignDefaultSelection:
    """A campaign-authored edge from a catalog selector to one concrete skill.

    The catalog owns the modifier. Campaign setup can select a target, but it
    cannot supply or alter the numerical default.
    """

    source: str
    selector: str
    target: str


@dataclass(frozen=True, slots=True)
class CampaignSkillSpecialty:
    """The concrete identity selected for one otherwise-open skill family."""

    family: str
    definition_id: str
    name: str
    specialty: str


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
    optional_rule: str | None = None

    def expand(self, parent: str, optional_rules: frozenset[str] = frozenset()) -> Technique:
        """The concrete parent-relative technique for one permitted parent."""
        if parent not in self.parents:
            raise ValueError(f"Parent is outside the template's permitted set: {parent}")
        if self.optional_rule is not None and self.optional_rule not in optional_rules:
            raise ValueError(f"Technique requires optional rule: {self.optional_rule}")
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
    # B168: a /TL skill purchase records the TL at which it was learned.
    technology_level_required: bool = False
