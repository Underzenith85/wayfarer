"""Square and hex battlefield templates authored by a scenario."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, ValidationInfo, model_validator

from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.models import Id, Record


class GridPoint(Record):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)


class Battlefield(Record):
    coordinate_system: Literal["square-grid-v1"] = Field(
        default="square-grid-v1", exclude_if=lambda v: True
    )
    id: Id
    location_id: Id
    width: int = Field(ge=1, le=1000)
    height: int = Field(ge=1, le=1000)
    blocked: tuple[GridPoint, ...] = ()
    darkness_penalty: int = Field(default=0, ge=-10, le=0, exclude_if=lambda v: v == 0)

    @model_validator(mode="after")
    def validate_grid(self) -> Battlefield:
        if len(set(self.blocked)) != len(self.blocked):
            raise ValueError("Duplicate blocked position")
        if any(point.x >= self.width or point.y >= self.height for point in self.blocked):
            raise ValueError("Blocked position is outside the battlefield")
        return self


def _tag_template(value: object, info: ValidationInfo) -> object:
    if isinstance(value, dict):
        value = {"coordinate_system": "square-grid-v1", **value}
        if info.mode == "json":
            model = HexBattlefield if value["coordinate_system"] == "hex-axial-v1" else Battlefield
            return model.model_validate_json(json.dumps(value))
    return value


BattlefieldTemplate = Annotated[
    Battlefield | HexBattlefield,
    Field(discriminator="coordinate_system"),
    BeforeValidator(_tag_template),
]
