"""Immutable character advancement and explicit rules-migration records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from wayfarer.character.compiler import CharacterDraft
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


class AdvancementEntry(Record):
    id: Id
    actor_id: Id
    kind: Literal["earned", "purchase", "refund"]
    points: int
    revision: int = Field(ge=1)
    build_before: str
    build_after: str
    reason: str = Field(min_length=1, max_length=2000)


class MigrationEntry(Record):
    id: Id
    actor_id: Id
    revision: int = Field(ge=1)
    from_digest: str
    to_digest: str
    reason: str = Field(min_length=1, max_length=2000)
    # Registered profile identities, when the migration selected a profile.
    from_profile: str | None = None
    to_profile: str | None = None


class BuildDiff(Record):
    actor_id: Id
    old_revision: str
    new_revision: str
    old_spent: int
    new_spent: int
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed_values: tuple[tuple[str, str, str], ...] = ()


class AdvancementPreview(Record):
    actor_id: Id
    draft: CharacterDraft
    points_available: int
    points_delta: int
    diff: BuildDiff


class MigrationPreview(Record):
    from_digest: str
    to_digest: str
    actor_diffs: tuple[BuildDiff, ...]


def validate_ledgers(state: PlayState) -> None:
    """Advancement and migration ledgers carry unique IDs and never lead the checkpoint."""
    if len({entry.id for entry in state.advancement}) != len(state.advancement):
        raise ValidationError("Duplicate advancement ledger ID")
    if len({entry.id for entry in state.migrations}) != len(state.migrations):
        raise ValidationError("Duplicate migration ledger ID")
    if any(entry.revision > state.revision for entry in state.advancement) or any(
        entry.revision > state.revision for entry in state.migrations
    ):
        raise ValidationError("Ledger entry is ahead of campaign state")
