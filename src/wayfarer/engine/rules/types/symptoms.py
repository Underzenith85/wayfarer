"""Persisted cumulative Symptoms and causal damage, Characters third printing B109."""

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Record


class SymptomSpec(Record):
    kind: Literal["blindness", "coughing", "attribute-penalty"]
    attribute: Literal["st", "dx", "iq", "ht"] | None = None
    level: int = Field(default=1, ge=1, le=100)
    numerator: Literal[1, 2] = 1
    denominator: Literal[2, 3] = 2

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.numerator, self.denominator) not in {(1, 3), (1, 2), (2, 3)}:
            raise ValueError("Unsupported Symptoms threshold")
        if (self.kind == "attribute-penalty") != (self.attribute is not None):
            raise ValueError("Only attribute penalties name an attribute")
        if self.kind != "attribute-penalty" and self.level != 1:
            raise ValueError("Named Symptoms have no levels")
        return self


class SymptomEffect(Record):
    id: str
    pool_id: str
    source_id: str
    actor_id: str
    spec: SymptomSpec
    active: bool = False


class SymptomDebt(Record):
    id: str
    pool_id: str
    source_id: str | None = None
    remaining: int = Field(ge=0)
    restriction_id: str | None = None
