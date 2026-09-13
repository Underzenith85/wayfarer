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


class HazardEnvironment(HazardRecord):
    """Measured, scenario-authored exposure facts (Campaigns B428-B437)."""

    medium: Literal[
        "contact",
        "air",
        "water",
        "fire",
        "electric-current",
        "acceleration",
        "radiation",
        "vacuum",
        "vessel-motion",
    ]
    intensity: int = Field(ge=1, le=100000)
    duration_seconds: int = Field(ge=1, le=31536000)
    pressure_milli_atmospheres: int | None = Field(default=None, ge=0, le=1000000)
    temperature_f: int | None = Field(default=None, ge=-1000, le=10000)
    source_class: str = Field(min_length=1, max_length=120)


class HazardProtection(HazardRecord):
    """Protection is explicit; absence never means a safe seal or air supply."""

    sealed: bool = False
    breathing_supply: bool = False
    eye_protection: bool = False
    insulated: bool = False
    pressure_support: int = Field(default=0, ge=0, le=3)
    vacuum_support: bool = False
    radiation_pf: int = Field(default=1, ge=1, le=1000000)
    nonmetallic_dr: int = Field(default=0, ge=0, le=1000)
    motion_stabilized: bool = False


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
    kind: Literal[
        "cold",
        "heat",
        "fire",
        "suffocation",
        "drowning",
        "pressure",
        "poison",
        "disease",
        "acid",
        "atmosphere",
        "electricity",
        "acceleration",
        "radiation",
        "seasickness",
        "vacuum",
    ]
    scene_id: str
    delay: int = Field(default=0, ge=0, le=31536000)
    interval: int = Field(default=1, ge=1, le=31536000)
    cycles: int = Field(default=1, ge=1, le=100000)
    cycles_dice: int = Field(default=0, ge=0, le=10)
    resistance_modifier: int = Field(default=0, ge=-30, le=30)
    damage_dice: int = Field(default=0, ge=0, le=100)
    damage_add: int = Field(default=1, ge=-10, le=1000)
    resistible: bool = True
    reference: str = Field(min_length=1)
    variant: str | None = None
    affliction: Literal["none", "coughing", "blindness", "paralysis", "retching", "seizure"] = (
        "none"
    )
    affliction_seconds: int = Field(default=0, ge=0)
    duration_per_margin: int = Field(default=0, ge=0)
    bacterial: bool = False
    drug_resistant: bool = False
    recovery_successes: int = Field(default=1, ge=1, le=100)
    environment: HazardEnvironment | None = None
    protection: HazardProtection | None = None
    damage_type: Literal["burn", "cor", "cr", "tox"] | None = None
    damage_from_margin: bool = False
    critical_effect: Literal["none", "unconscious", "heart-attack", "death"] = "none"
    radiation_rads: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode="after")
    def supported_variant(self) -> HazardSpec:
        if self.kind in ("cold", "heat") and (
            self.interval
            not in (
                (60, 600, 900, 1800)
                if self.kind == "cold" and self.variant == "thermal-shock"
                else (600, 900, 1800)
                if self.kind == "cold"
                else (1, 1800)
                if self.variant == "intense-heat"
                else (1800,)
            )
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
        extended = {
            "acid",
            "atmosphere",
            "electricity",
            "acceleration",
            "radiation",
            "seasickness",
            "vacuum",
        }
        if self.kind in extended and (self.environment is None or self.protection is None):
            raise ValueError(
                "Environmental variants require explicit exposure and protection facts"
            )
        if self.kind == "radiation" and self.radiation_rads < 1:
            raise ValueError("Radiation exposure requires an authored positive dose")
        return self


class CombatHazardTurn(HazardRecord):
    """An actor-relative deadline on the shared combat clock (B404)."""

    encounter_id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    round: int = Field(ge=1)


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
    resistance_bonus: int = 0
    survival: int | None = Field(default=None, ge=1)
    treatment_bonus: int = Field(default=0, ge=0)
    symptoms: int = Field(default=0, ge=0)
    full_hp: int = Field(default=10, ge=1)
    affliction_until: int = Field(default=0, ge=0)
    immune: bool = False
    diagnosed_by: tuple[str, ...] = ()
    active: bool = True
    cycle: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    stage: Literal["exposure", "cycles", "struggling", "recovering", "swimming", "rescued"] = (
        "cycles"
    )
    no_air_since: int | None = Field(default=None, ge=0)
    next_check_at: int | None = Field(default=None, ge=0)
    combat_turn: CombatHazardTurn | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    radiation_dose: int = Field(default=0, ge=0)
    radiation_original: int = Field(default=0, ge=0)
    radiation_received_at: int | None = Field(default=None, ge=0)
    radiation_checked_at: int | None = Field(default=None, ge=0)
    radiation_decayed_at: int | None = Field(default=None, ge=0)
    adapted: bool = False
    conditions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def combat_timing(self) -> HazardSchedule:
        if self.combat_turn is not None and self.spec.kind != "suffocation":
            raise ValueError("Actor-relative hazards currently require suffocation")
        return self


def require_hazards_settled(
    hazards: tuple[HazardSchedule, ...], actors: frozenset[str], at: int
) -> None:
    if any(
        h.active and h.combat_turn is None and h.actor_id in actors and h.due <= at for h in hazards
    ):
        raise ConflictError("Resolve due environmental hazards before further activity")
