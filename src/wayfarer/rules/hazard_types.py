"""Persisted environmental exposure and per-cause recovery restrictions (#110)."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError
from wayfarer.models import Record


class HazardRecord(Record):
    """Base for hazard exposure rows."""


class RecoveryRestriction(HazardRecord):
    id: str
    actor_id: str
    active: bool = True
    hp_debt: int = Field(default=0, ge=0)
    fp_debt: int = Field(default=0, ge=0)
    blocks_natural_healing: bool = True
    blocks_physician_healing: bool = False
    blocks_rest: bool = False


def blocked_hp(illnesses: tuple[RecoveryRestriction, ...], actor_id: str, kind: str) -> int:
    if kind in ("stabilize", "resuscitate"):
        return 0
    return sum(
        illness.hp_debt
        for illness in illnesses
        if illness.active
        and illness.actor_id == actor_id
        and (
            illness.blocks_natural_healing
            if kind == "natural"
            else illness.blocks_physician_healing
        )
    )


def blocked_fp(illnesses: tuple[RecoveryRestriction, ...], actor_id: str) -> int:
    return sum(
        i.fp_debt for i in illnesses if i.active and i.actor_id == actor_id and i.blocks_rest
    )


class HazardSpec(HazardRecord):
    """Scenario-owned configuration, never accepted as a player command payload."""

    id: str = Field(min_length=1, max_length=120)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    kind: Literal["cold", "heat", "fire", "suffocation", "drowning", "poison", "disease"]
    scene_id: str
    delay: int = Field(default=0, ge=0, le=31536000)
    interval: int = Field(default=1, ge=1, le=31536000)
    cycles: int = Field(default=1, ge=1, le=100000)
    resistance_modifier: int = Field(default=0, ge=-30, le=30)
    damage_dice: int = Field(default=0, ge=0, le=100)
    damage_add: int = Field(default=1, ge=-10, le=1000)
    resistible: bool = True
    reference: str = Field(min_length=1)
    recovery_successes: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def supported_variant(self) -> HazardSpec:
        if self.kind in ("cold", "heat") and (
            self.interval not in ((600, 900, 1800) if self.kind == "cold" else (1800,))
            or self.damage_dice
            or self.damage_add != 1
            or not self.resistible
        ):
            raise ValueError("Unsupported ambient exposure variant")
        if self.kind in ("suffocation", "drowning") and (
            self.interval != (1 if self.kind == "suffocation" else 5)
            or self.damage_dice
            or self.damage_add != 1
            or self.resistible != (self.kind == "drowning")
        ):
            raise ValueError("Unsupported breathing hazard variant")
        if self.kind == "suffocation" and (self.delay != 1 or self.cycles < 240):
            raise ValueError("No-air exposure must cover the four-minute death deadline")
        return self


class HazardSchedule(HazardRecord):
    id: str
    actor_id: str
    spec: HazardSpec
    started: int = Field(ge=0)
    due: int = Field(ge=0)
    remaining: int = Field(ge=0)
    ht: int = Field(ge=1)
    will: int = Field(ge=1)
    swimming: int = Field(ge=1)
    resistance: int = Field(default=0, ge=0)
    active: bool = True
    cycle: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    stage: Literal["exposure", "cycles", "struggling", "recovering", "swimming"] = "cycles"
    no_air_since: int | None = Field(default=None, ge=0)
    next_check_at: int | None = Field(default=None, ge=0)


def require_hazards_settled(
    hazards: tuple[HazardSchedule, ...], actors: frozenset[str], at: int
) -> None:
    if any(h.active and h.actor_id in actors and h.due <= at for h in hazards):
        raise ConflictError("Resolve due environmental hazards before further activity")
