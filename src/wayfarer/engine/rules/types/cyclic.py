"""Persisted Cyclic attack occurrences and resource clock guards (B103-104)."""

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.disease import ContactExposure
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.errors import ConflictError
from wayfarer.models import Record


class CyclicAttack(Record):
    id: str
    attacker_id: str
    actor_id: str
    attack_id: str
    basic_damage: int = Field(ge=1)
    damage_dice: int = Field(ge=1, le=1000)
    damage_type: Literal["burn", "cor", "fat", "tox"]
    resistance: int = Field(ge=0)
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0)
    vulnerability_multiplier: int = Field(default=1, ge=1, le=4)
    ht: int = Field(ge=1)
    resistance_modifier: int | None = None
    interval: int = Field(ge=1)
    remaining: int = Field(ge=0)
    due: int = Field(ge=0)
    stop_condition: str = Field(min_length=1)
    active: bool = True
    cycle: int = Field(default=1, ge=0)
    hp_debt: int = Field(default=0, ge=0)
    fp_debt: int = Field(default=0, ge=0)
    contagious: Literal["none", "mild", "high"] = "none"
    contagion_vector: Literal["blood", "contact", "digestive", "respiratory"] | None = None
    symptom_spec: SymptomSpec | None = None
    symptom_source_id: str | None = None
    incubation_seconds: int = Field(default=86400, ge=1, le=31536000)

    @model_validator(mode="after")
    def valid(self) -> CyclicAttack:
        if self.active and self.remaining == 0:
            raise ValueError("Active Cyclic attack requires remaining cycles")
        return self


def require_cyclic_settled(attacks: tuple[CyclicAttack, ...], at: int) -> None:
    if any(a.active and a.due < at for a in attacks):
        raise ConflictError("Settle the Cyclic deadline before advancing further")


class CyclicOccurrence(Record):
    id: str
    attack_id: str
    actor_id: str
    at: int = Field(ge=0)
    cycle: int = Field(ge=1)
    damage_dice: tuple[int, ...] = ()
    check: CheckTrace | None = None
    hp_lost: int = Field(default=0, ge=0)
    fp_lost: int = Field(default=0, ge=0)


class CyclicExposure(Record):
    id: str
    source: CyclicAttack
    relationship: ContactExposure
    ht: int = Field(ge=1)
    due: int = Field(ge=0)
    stage: Literal["exposure", "resisted", "infected"] = "exposure"
    check: CheckTrace | None = None
    infection_id: str | None = None
    immune: bool = False


def require_exposures_settled(exposures: tuple[CyclicExposure, ...], at: int) -> None:
    if any(e.stage == "exposure" and e.due < at for e in exposures):
        raise ConflictError("Settle the Cyclic contagion deadline before advancing further")
