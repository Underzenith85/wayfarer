"""Strict trusted environment for the native B355/B443 care host."""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError
from wayfarer.models import Record


@dataclass(frozen=True)
class HazardCare:
    kind: Literal["lifesaving", "diagnosis", "antibiotics"]
    target_id: str
    schedule_id: str
    item_id: str | None = None
    technology_level: int = 0
    known_disease: bool = True
    safe_landing: bool = False


class CapturedCare(Record):
    kind: Literal["lifesaving", "diagnosis", "antibiotics"]
    target_id: str = Field(min_length=1)
    schedule_id: str = Field(min_length=1)
    item_id: str | None
    technology_level: int = Field(ge=0, le=12)
    known_disease: bool
    safe_landing: bool


def captured(raw: object) -> HazardCare:
    try:
        value = CapturedCare.model_validate(raw)
    except SchemaError as exc:
        raise ValidationError("Invalid captured hazard care context") from exc
    return HazardCare(
        value.kind,
        value.target_id,
        value.schedule_id,
        value.item_id,
        value.technology_level,
        value.known_disease,
        value.safe_landing,
    )


def lifesaving_target(base: int, modifier: int, *, current: bool) -> int:
    """B345 admission uses the same complete B355 score the actual roll consumes."""
    if not current:
        return max(1, base)
    if base + modifier < 3:
        raise ValidationError("Lifesaving requires effective skill at least 3")
    return base


def diagnosis_target(base: int, modifier: int, *, current: bool) -> int:
    """B345 uses the complete voluntary Diagnosis score, preserving old inputs."""
    if not current:
        return max(1, base)
    if base + modifier < 3:
        raise ValidationError("Diagnosis requires effective skill at least 3")
    return base


class CareCommandInput(Record):
    id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    treatment_id: str


def validate_command(raw: object) -> None:
    try:
        CareCommandInput.model_validate(raw)
    except SchemaError as exc:
        raise ValidationError("Invalid captured hazard care command") from exc
