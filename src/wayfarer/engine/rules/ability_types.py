"""Domain-only catalog binding for the representative ability runtime."""

from typing import Literal

from pydantic import Field

from wayfarer.models import Record


class AbilitySpec(Record):
    definition_id: str = Field(min_length=1, max_length=200)
    kind: Literal["burning-malediction", "damage-resistance", "detect", "mind-reading"]
    modifiers: tuple[str, ...] = ()
