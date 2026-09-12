"""Executable bindings for the selected mundane traits; unbound effects stay off.

An effect appears here only when an existing authoritative service already
resolves it: reaction and influence modifiers (Characters, Fourth Edition,
third printing, B23-30, B41, B97; Campaigns B359 for influence) and the
self-control roll (B120-121). The bindings supply trusted integers, never a
client formula, and every remaining interaction stays an item-level blocker.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from wayfarer.engine.rules.traits.background import BACKGROUND_HOOKS
from wayfarer.engine.rules.traits.mental import MENTAL_HOOKS
from wayfarer.engine.rules.traits.physical import PHYSICAL_HOOKS

Check = Literal["reaction", "influence"]
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
AppearanceOption = Literal["ordinary", "androgynous", "impressive"]
Perception = Literal["perceptible", "audible", "status"]
SELF_CONTROL_HOOK: Final = "trait.self_control"


@dataclass(frozen=True, slots=True)
class Audience:
    """Trusted server description of the reacting party, never a player claim.

    `attracted`, `visible`, and `appearance_applicable` control appearance
    reactions (B21); `classes` selects reputation audiences. These describe
    the same observer as the other social modifiers.
    """

    perceptible: bool = True
    audible: bool = True
    recognizes_status: bool = True
    attracted: bool = False
    classes: tuple[str, ...] = ()
    visible: bool = True
    appearance_applicable: bool = True
    appearance_resentment: bool = False
    same_culture: bool = True
    nuisance_interest: bool = False
    observer_status: int = 0
    status_disposition: Literal["friendly", "neutral", "angry", "resentful"] = "neutral"


DEFAULT_AUDIENCE: Final = Audience()


@dataclass(frozen=True, slots=True)
class ReactionBinding:
    """One executable reaction/influence contribution of an approved trait."""

    hook: str
    per_level: int
    perception: Perception
    checks: tuple[Check, ...]
    page: int
    blockers: tuple[str, ...] = ()

    @property
    def reference(self) -> str:
        return f"B{self.page}"

    def applies(self, check: Check, audience: Audience) -> bool:
        if check not in self.checks:
            return False
        return {
            "perceptible": audience.perceptible,
            "audible": audience.audible,
            "status": audience.recognizes_status,
        }[self.perception]


@dataclass(frozen=True, slots=True)
class AppearanceBinding:
    level: Appearance
    option: AppearanceOption = "ordinary"
    universal: bool = False
    off_the_shelf: bool = False


@dataclass(frozen=True, slots=True)
class ReputationBinding:
    level: int
    scope: Literal["everyone", "large-class", "small-class"] = "everyone"
    recognition: Literal["always", "sometimes", "occasionally"] = "always"
    classes: tuple[str, ...] = ()


REACTION_BINDINGS: Final = MappingProxyType(
    {
        "trait:charisma": ReactionBinding(
            "trait.social_modifiers", 1, "perceptible", ("reaction", "influence"), 41
        ),
        # Influence-skill procedures own the B97 +2 and approved builds assert
        # their audible condition through character.social_traits.
        "trait:voice": ReactionBinding("trait.voice", 2, "audible", ("reaction",), 97),
        "trait:status": ReactionBinding(
            "trait.status",
            1,
            "status",
            ("reaction", "influence"),
            28,
        ),
        "trait:low-status": ReactionBinding(
            "trait.status",
            -1,
            "status",
            ("reaction", "influence"),
            28,
        ),
    }
)
APPEARANCE_BINDINGS: Final[MappingProxyType[str, AppearanceBinding]] = MappingProxyType(
    {
        f"trait:appearance-{level}": AppearanceBinding(level)
        for level in (
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
        )
    }
    | {
        f"trait:appearance-{level}-{option}": AppearanceBinding(level, option)
        for level in ("handsome", "very-handsome", "transcendent")
        for option in ("androgynous", "impressive")
    }
    | {
        f"trait:appearance-{level}-universal": AppearanceBinding(level, "ordinary", True)
        for level in ("attractive", "handsome", "very-handsome", "transcendent")
    }
    | {
        f"trait:appearance-{level}-off-the-shelf": AppearanceBinding(level, "ordinary", False, True)
        for level in ("handsome", "very-handsome", "transcendent")
    }
)
# These selected constructions are recognized by everyone, always (B26-28).
# Restricted audiences and recognition frequency are separate constructions.
REPUTATION_BINDINGS: Final = MappingProxyType(
    {
        "trait:reputation-bravery": ReputationBinding(1),
        "trait:reputation-cruelty": ReputationBinding(-1),
        "trait:reputation-bravery-guild-sometimes": ReputationBinding(
            2, "large-class", "sometimes", ("guild",)
        ),
        "trait:reputation-cruelty-guild-occasionally": ReputationBinding(
            -2, "small-class", "occasionally", ("guild",)
        ),
    }
)
STANDING_HOOKS: Final = frozenset({"trait.appearance", "trait.reputation"})
SUPPORTED_HOOKS: Final = frozenset(
    {SELF_CONTROL_HOOK}
    | BACKGROUND_HOOKS
    | {binding.hook for binding in REACTION_BINDINGS.values()}
    | STANDING_HOOKS
    | MENTAL_HOOKS
    | PHYSICAL_HOOKS
)
