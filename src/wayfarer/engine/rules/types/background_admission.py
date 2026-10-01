"""A GM's explicit B96 campaign decision, persisted in the pinned rules package."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record

UNUSUAL_BACKGROUND_ID = "trait:advantage:unusual-background"
BACKGROUND_ADMISSION_HOOK = "campaign.unusual-background"


class UnusualBackgroundDecision(Record):
    id: Id
    gm_id: Id
    description: str = Field(min_length=1, max_length=2000)
    point_cost: int = Field(ge=0)
    benefits: tuple[Id, ...] = Field(min_length=1, max_length=100)
    allowed: bool = True
    reference: Literal["Characters third printing B96"] = "Characters third printing B96"

    @model_validator(mode="after")
    def distinct_benefits(self) -> Self:
        if len(set(self.benefits)) != len(self.benefits) or UNUSUAL_BACKGROUND_ID in self.benefits:
            raise ValueError("Unusual Background requires distinct non-background benefits")
        if not self.description.strip():
            raise ValueError("The GM must describe the tangible benefit")
        return self
