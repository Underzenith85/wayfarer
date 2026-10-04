"""Server-bound care environment values, independent of capture and services."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.orchestration.play import PlayService


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


EnvironmentResolver = Callable[[PlayService, PlayState, str], CareEnvironment]
