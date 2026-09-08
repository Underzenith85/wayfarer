"""Status, reputation and appearance hooks for reaction and influence rolls.

Intended sources: Basic Set Characters, Fourth Edition, B21 (Appearance),
B26-27 (Reputation), B28 (Status), B41 (Charisma), B97 (Voice), with the frozen
2004 first printing / 2007-01-26 errata baseline. Reconstructed from model
knowledge under the owner's explicit provisional-implementation authorization;
exact printing verification remains an audit blocker. No rulebook prose here.

The engine owns this arithmetic so that a scenario, a character sheet or a
generated proposal cannot invent a reaction modifier: authored data selects a
declared standing, and the values below are derived from it. Purchase legality
and point costs belong to trait compilation (#100) and catalog content (#113);
this module derives play-time reaction modifiers only.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import RandomSource, draw_dice
from wayfarer.rules.conformance import capability, profile
from wayfarer.rules.gurps_social import ReactionModifier

Appearance = Literal[
    "horrific",
    "monstrous",
    "hideous",
    "ugly",
    "unattractive",
    "average",
    "attractive",
    "handsome",
    "very-handsome",
    "transcendent",
]
ReputationScope = Literal["everyone", "large-class", "small-class"]
Recognition = Literal["always", "sometimes", "occasionally"]

APPEARANCE_REACTIONS: Final = MappingProxyType(
    {
        # level: (indifferent observer, observer attracted to this character)
        "horrific": (-6, -6),
        "monstrous": (-5, -5),
        "hideous": (-4, -4),
        "ugly": (-2, -2),
        "unattractive": (-1, -1),
        "average": (0, 0),
        "attractive": (1, 1),
        "handsome": (2, 4),
        "very-handsome": (2, 6),
        "transcendent": (2, 8),
    }
)
"""Negative levels repel every observer; only the top three levels split."""

RECOGNITION_TARGETS: Final = MappingProxyType({"always": None, "sometimes": 10, "occasionally": 7})
"""A reputation recognized less than always is rolled for on 3d, once per check."""

STATUS_LEVELS: Final = (-2, 8)
CHARISMA_LEVELS: Final = (0, 10)
REPUTATION_LEVELS: Final = (-4, 4)
VOICE_REACTION: Final = 2
CAPABILITY: Final = "gurps.social.reaction"


@dataclass(frozen=True, slots=True)
class Reputation:
    """One declared reputation: who recognizes it, how often, and for how much."""

    id: str
    level: int
    scope: ReputationScope = "everyone"
    recognition: Recognition = "always"
    classes: tuple[str, ...] = ()
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class Standing:
    """The acting character's declared social standing, derived from approved traits."""

    appearance: Appearance = "average"
    status: int = 0
    charisma: int = 0
    voice: bool = False
    reputations: tuple[Reputation, ...] = ()


@dataclass(frozen=True, slots=True)
class Audience:
    """What the reacting subject perceives; never a player choice or a secret fact."""

    recognizes_status: bool = True
    attracted: bool = False
    classes: tuple[str, ...] = ()
    sees_appearance: bool = True
    hears_voice: bool = True


ANY_OBSERVER: Final = Audience()
"""The default observer: sees, hears, and recognizes ordinary public standing."""


@dataclass(frozen=True, slots=True)
class RecognitionRoll:
    reputation_id: str
    dice: tuple[int, int, int]
    total: int
    target: int
    recognized: bool


@dataclass(frozen=True, slots=True)
class StandingTrace:
    modifiers: tuple[ReactionModifier, ...]
    recognition: tuple[RecognitionRoll, ...] = ()

    @property
    def total(self) -> int:
        return sum(modifier.value for modifier in self.modifiers)

    @property
    def public_modifiers(self) -> tuple[ReactionModifier, ...]:
        """The rows a narrator may describe; secret standing never leaves the receipt."""
        return tuple(modifier for modifier in self.modifiers if not modifier.hidden)


def supported_appearance(profile_id: str) -> tuple[str, ...]:
    """Expose the declared levels so validators cannot accept an invented one."""
    profile(profile_id)
    capability(CAPABILITY)
    return tuple(APPEARANCE_REACTIONS)


def _bounded(value: object, bounds: tuple[int, int], label: str) -> int:
    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
        raise ValidationError(f"{label} must be an integer within {bounds[0]}..{bounds[1]}")
    return value


def validate_standing(standing: Standing) -> Standing:
    """Reject any standing the declared hooks cannot derive a modifier from."""
    if standing.appearance not in APPEARANCE_REACTIONS:
        raise ValidationError("Unsupported appearance level")
    _bounded(standing.status, STATUS_LEVELS, "Status")
    _bounded(standing.charisma, CHARISMA_LEVELS, "Charisma")
    if type(standing.voice) is not bool:
        raise ValidationError("Voice must be declared as a boolean")
    identifiers = [reputation.id for reputation in standing.reputations]
    if len(set(identifiers)) != len(identifiers) or not all(identifiers):
        raise ValidationError("Reputations require unique identifiers")
    for reputation in standing.reputations:
        if _bounded(reputation.level, REPUTATION_LEVELS, "Reputation") == 0:
            raise ValidationError("A reputation of 0 has no reaction effect")
        if reputation.scope not in ("everyone", "large-class", "small-class"):
            raise ValidationError("Unsupported reputation scope")
        if reputation.recognition not in RECOGNITION_TARGETS:
            raise ValidationError("Unsupported reputation recognition frequency")
        classes = reputation.classes
        if (reputation.scope == "everyone") != (not classes):
            raise ValidationError("Only a class-scoped reputation names affected classes")
        if len(set(classes)) != len(classes) or not all(classes):
            raise ValidationError("Reputation classes must be unique identifiers")
    return standing


def standing_modifiers(
    profile_id: str, standing: Standing, audience: Audience, *, rng: RandomSource
) -> StandingTrace:
    """Derive typed reaction modifiers, rolling only for uncertain recognition.

    Dice are consumed in declared reputation order and before the reaction or
    influence roll itself, so a recorded receipt replays exactly. An unrecognized
    reputation contributes no modifier and no hint of itself to the player.
    """
    profile(profile_id)
    capability(CAPABILITY)
    validate_standing(standing)
    modifiers: list[ReactionModifier] = []
    recognition: list[RecognitionRoll] = []
    if audience.sees_appearance:
        indifferent, attracted = APPEARANCE_REACTIONS[standing.appearance]
        value = attracted if audience.attracted else indifferent
        if value:
            modifiers.append(
                ReactionModifier("appearance", value, f"appearance:{standing.appearance}")
            )
    if standing.status and audience.recognizes_status:
        modifiers.append(ReactionModifier("status", standing.status, "status"))
    for reputation in standing.reputations:
        if reputation.scope != "everyone" and not set(reputation.classes) & set(audience.classes):
            continue
        target = RECOGNITION_TARGETS[reputation.recognition]
        if target is not None:
            dice = draw_dice(rng)
            roll = RecognitionRoll(reputation.id, dice, sum(dice), target, sum(dice) <= target)
            recognition.append(roll)
            if not roll.recognized:
                continue
        modifiers.append(
            ReactionModifier(
                "reputation", reputation.level, f"reputation:{reputation.id}", reputation.hidden
            )
        )
    if standing.charisma:
        modifiers.append(ReactionModifier("trait", standing.charisma, "trait:charisma"))
    if standing.voice and audience.hears_voice:
        modifiers.append(ReactionModifier("trait", VOICE_REACTION, "trait:voice"))
    return StandingTrace(tuple(modifiers), tuple(recognition))
