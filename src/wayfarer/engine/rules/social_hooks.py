"""Shared appearance and reputation reaction resolver.

Intended sources: Basic Set Characters, Fourth Edition, B21 (Appearance) and
B26-27 (Reputation), on the selected Characters third-printing baseline.
Reconstructed from model knowledge under the owner's explicit provisional-
implementation authorization; mechanics verification remains an audit blocker.
No rulebook prose here.

Status, Charisma and Voice are *not* declared here: `rules.mundane_traits.runtime`
binds them to approved purchases of pinned definitions (#113). Selected
appearance and reputation purchases also use this resolver through
character.social_traits.bind_standing. Legacy actors without those purchases
retain authored standing; a purchased source cannot also be supplied by a
scenario resolver. Values are derived by rule, never supplied as a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from math import floor
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.engine.rules.checks import RandomSource, draw_dice
from wayfarer.engine.rules.conformance import capability, profile
from wayfarer.engine.rules.gurps_social import ReactionModifier
from wayfarer.engine.rules.mundane_traits.runtime import DEFAULT_AUDIENCE, Audience
from wayfarer.engine.rules.mundane_traits.runtime import Appearance as Appearance
from wayfarer.errors import ValidationError

ReputationScope = Literal["everyone", "large-class", "small-class"]
Recognition = Literal["always", "sometimes", "occasionally"]
AppearanceOption = Literal["ordinary", "androgynous", "impressive"]
EMPTY_RECOGNITION: Final[Mapping[str, RecognitionRoll]] = MappingProxyType({})

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
    appearance_option: AppearanceOption = "ordinary"
    universal_appearance: bool = False
    off_the_shelf_appearance: bool = False


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
    consequences: tuple[str, ...] = ()

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
    if standing.appearance_option != "ordinary" and standing.appearance not in (
        "attractive",
        "handsome",
        "very-handsome",
        "transcendent",
    ):
        raise ValidationError("Appearance option requires above-average appearance")
    if standing.off_the_shelf_appearance and standing.appearance not in (
        "handsome",
        "very-handsome",
        "transcendent",
    ):
        raise ValidationError("Off-the-Shelf Looks requires appearance above Attractive")
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
    known_recognition: Mapping[str, RecognitionRoll] = EMPTY_RECOGNITION,
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
    consequences: tuple[str, ...] = ()
    if (
        audience.perceptible
        and audience.visible
        and (audience.appearance_applicable or standing.universal_appearance)
    ):
        indifferent, attracted = APPEARANCE_REACTIONS[standing.appearance]
        if standing.appearance_option != "ordinary" or standing.universal_appearance:
            value = {"handsome": 3, "very-handsome": 4, "transcendent": 5}.get(
                standing.appearance, indifferent
            )
        else:
            value = attracted if audience.attracted else indifferent
        if (
            standing.appearance in ("very-handsome", "transcendent")
            and audience.appearance_resentment
        ):
            value = -2
        if standing.off_the_shelf_appearance and audience.same_culture:
            value = int(value / 2)
        if standing.appearance in ("very-handsome", "transcendent") and audience.nuisance_interest:
            consequences = ("attention-from-nuisances",)
        if value:
            modifiers.append(
                ReactionModifier("appearance", value, f"appearance:{standing.appearance}")
            )
    for reputation in standing.reputations:
        if reputation.scope != "everyone" and not set(reputation.classes) & set(audience.classes):
            continue
        target = RECOGNITION_TARGETS[reputation.recognition]
        if target is not None:
            roll = known_recognition.get(reputation.id)
            if roll is None:
                dice = draw_dice(rng)
                roll = RecognitionRoll(reputation.id, dice, sum(dice), target, sum(dice) <= target)
            recognition.append(roll)
            if not roll.recognized:
                continue
        previous = sum(modifier.value for modifier in modifiers if modifier.kind == "reputation")
        capped = max(-4, min(4, previous + reputation.level))
        contribution = capped - previous
        if contribution:
            modifiers.append(
                ReactionModifier(
                    "reputation",
                    contribution,
                    f"reputation:{reputation.id}",
                    reputation.hidden,
                )
            )
    return StandingTrace(tuple(modifiers), tuple(recognition), consequences)


def reputation_cost(level: int, scope: ReputationScope, recognition: Recognition) -> int:
    """Apply each construction discount and round down after each step (B26-28)."""
    if type(level) is not int or level == 0 or not -4 <= level <= 4:
        raise ValidationError("Reputation level must be a nonzero integer within -4..4")
    if scope not in ("everyone", "large-class", "small-class"):
        raise ValidationError("Unsupported reputation scope")
    if recognition not in ("always", "sometimes", "occasionally"):
        raise ValidationError("Unsupported recognition frequency")
    scope_multiplier = {
        "everyone": Fraction(1),
        "large-class": Fraction(1, 2),
        "small-class": Fraction(1, 3),
    }[scope]
    recognition_multiplier = {
        "always": Fraction(1),
        "sometimes": Fraction(1, 2),
        "occasionally": Fraction(1, 3),
    }[recognition]
    magnitude = floor(abs(level) * 5 * scope_multiplier)
    magnitude = floor(magnitude * recognition_multiplier)
    return magnitude if level > 0 else -magnitude
