"""Domain-only catalog binding for the representative ability runtime."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AbilitySpec(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    definition_id: str = Field(min_length=1, max_length=200)
    kind: Literal["burning-malediction", "damage-resistance", "detect", "mind-reading"]
    modifiers: tuple[str, ...] = ()
