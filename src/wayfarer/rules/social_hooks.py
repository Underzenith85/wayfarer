"""Shared appearance and reputation reaction resolver.

Intended sources: Basic Set Characters, Fourth Edition, B21 (Appearance) and
B26-27 (Reputation), on the frozen 2004 first printing / 2007-01-26 errata
baseline. Reconstructed from model knowledge under the owner's explicit
provisional-implementation authorization; exact printing verification remains an
audit blocker. No rulebook prose here.

Status, Charisma and Voice are *not* declared here: `rules.mundane_traits.runtime`
binds them to approved purchases of pinned definitions (#113). Selected
appearance and reputation purchases also use this resolver through
character.social_traits.bind_standing. Legacy actors without those purchases
retain authored standing; a purchased source cannot also be supplied by a
scenario resolver. Values are derived by rule, never supplied as a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import RandomSource, draw_dice
from wayfarer.rules.conformance import capability, profile
from wayfarer.rules.gurps_social import ReactionModifier
from wayfarer.rules.mundane_traits.runtime import DEFAULT_AUDIENCE, Audience
from wayfarer.rules.mundane_traits.runtime import Appearance as Appearance

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

REPUTATION_LEVELS: Final = (-4, 4)
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
    """The declared standing of the character being reacted to."""

    appearance: Appearance = "average"
    reputations: tuple[Reputation, ...] = ()


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


def validate_standing(standing: Standing) -> Standing:
    """Reject any standing the declared hooks cannot derive a modifier from."""
    if standing.appearance not in APPEARANCE_REACTIONS:
        raise ValidationError("Unsupported appearance level")
    identifiers = [reputation.id for reputation in standing.reputations]
    if len(set(identifiers)) != len(identifiers) or not all(identifiers):
        raise ValidationError("Reputations require unique identifiers")
    for reputation in standing.reputations:
        level = reputation.level
        if type(level) is not int or not REPUTATION_LEVELS[0] <= level <= REPUTATION_LEVELS[1]:
            raise ValidationError("Reputation must be an integer within -4..4")
        if level == 0:
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
    profile_id: str,
    standing: Standing,
    audience: Audience = DEFAULT_AUDIENCE,
    *,
    rng: RandomSource,
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
    if audience.perceptible and audience.visible and audience.appearance_applicable:
        indifferent, attracted = APPEARANCE_REACTIONS[standing.appearance]
        value = attracted if audience.attracted else indifferent
        if value:
            modifiers.append(
                ReactionModifier("appearance", value, f"appearance:{standing.appearance}")
            )
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
    return StandingTrace(tuple(modifiers), tuple(recognition))
