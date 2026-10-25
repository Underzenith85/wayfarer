"""Trusted care observations shared by capture, strict input identity and replay."""

import json
from dataclasses import dataclass, fields
from typing import ClassVar, Literal

from pydantic import ConfigDict, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ValidationError


@dataclass(frozen=True)
class CareEnvironment:
    technology_level: int = 8
    food: bool = False
    water: bool = False
    sleep: bool = False
    physician_id: str | None = None
    surgical_facility: bool = False
    anesthetic: bool = True
    surgical_modifier: int = 0
    life_support: bool = False
    sterile: bool = True
    equipment_quality_modifier: int = 0
    infection_risk: bool = False
    infection_modifier: int = 0
    resuscitation_first_aid_penalty: int = 4
    drug_item_id: str | None = None
    drug_form: Literal["pill", "contact", "aerosol", "injection"] | None = None
    drug_hp: int = 0
    drug_fp: int = 0
    mana: Literal["none", "low", "normal", "high", "very-high"] | None = None


@dataclass(frozen=True)
class _CapturedEnvironment(CareEnvironment):
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(strict=True, extra="forbid")


_CAPTURED = TypeAdapter(_CapturedEnvironment)


def captured(raw: object) -> CareEnvironment:
    if not isinstance(raw, dict) or set(raw) != {f.name for f in fields(CareEnvironment)}:
        raise ValidationError("Recorded medical environment fields are incomplete or unknown")
    try:
        return _CAPTURED.validate_json(json.dumps(raw))
    except SchemaError as exc:
        raise ValidationError("Recorded medical care environment is invalid") from exc
