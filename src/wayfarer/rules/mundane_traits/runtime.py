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

Check = Literal["reaction", "influence"]
Perception = Literal["perceptible", "audible", "status"]
SELF_CONTROL_HOOK: Final = "trait.self_control"


@dataclass(frozen=True, slots=True)
class Audience:
    """Trusted server description of the reacting party, never a player claim.

    `attracted` and `classes` describe the same observer for the appearance and
    reputation hooks (#111); no binding below reads them.
    """

    perceptible: bool = True
    audible: bool = True
    recognizes_status: bool = True
    attracted: bool = False
    classes: tuple[str, ...] = ()


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


REACTION_BINDINGS: Final = MappingProxyType(
    {
        "trait:charisma": ReactionBinding(
            "trait.social_modifiers", 1, "perceptible", ("reaction", "influence"), 41
        ),
        # Voice also modifies several influence skills. That skill bonus is not
        # implemented, so this binding stays out of influence rolls entirely
        # rather than approximating the unimplemented half.
        "trait:voice": ReactionBinding(
            "trait.voice", 2, "audible", ("reaction",), 97, ("voice-influence-skill-bonus",)
        ),
        "trait:status": ReactionBinding(
            "trait.status",
            1,
            "status",
            ("reaction", "influence"),
            28,
            ("free-status-from-wealth-or-rank",),
        ),
        "trait:low-status": ReactionBinding(
            "trait.status",
            -1,
            "status",
            ("reaction", "influence"),
            28,
            ("free-status-from-wealth-or-rank",),
        ),
    }
)
# Rank, reputation and appearance reaction sources remain unbound: their
# audiences and free-level interactions are not decided by this selection.
SUPPORTED_HOOKS: Final = frozenset(
    {SELF_CONTROL_HOOK} | {binding.hook for binding in REACTION_BINDINGS.values()}
)
