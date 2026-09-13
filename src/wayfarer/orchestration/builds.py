"""The canonical compiled build of an actor, and the points it has banked.

Both answers are read by the services that award, spend and migrate points, so
they sit below all of them rather than inside any one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def canonical_build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next((candidate for candidate in state.actors if candidate.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Unknown character")
    build = play.engine.reviewer.review(actor.proposal).compilation.build
    if build is None:
        raise ValidationError("Canonical character no longer compiles")
    return build


def banked_points(state: PlayState, actor_id: str) -> int:
    return sum(entry.points for entry in state.advancement if entry.actor_id == actor_id)


def spendable_points(
    state: PlayState, actor_id: str, changed_definition_ids: frozenset[str]
) -> int:
    """Return points legal for this revision, consuming discretionary credit first.

    Purchases are negative discretionary entries. That makes them consume the
    unrestricted balance first and then offset only source-bound awards eligible
    for the definitions changed by the proposed revision.
    """
    entries = tuple(entry for entry in state.advancement if entry.actor_id == actor_id)
    unrestricted = sum(entry.points for entry in entries if not entry.eligible_definition_ids)
    restricted = sum(
        entry.points
        for entry in entries
        if entry.points > 0
        and entry.eligible_definition_ids
        and changed_definition_ids.intersection(entry.eligible_definition_ids)
    )
    return max(0, unrestricted) + restricted
