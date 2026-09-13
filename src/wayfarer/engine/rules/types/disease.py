"""Persisted disease, wound-infection, and aging facts (Campaigns B442-B444)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.models import Record

PROFILE_ID: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
YEAR_SECONDS = 31_536_000

ContactKind = Literal[
    "avoided",
    "shared-building",
    "close-conversation",
    "brief-touch",
    "shared-material",
    "cooked-flesh",
    "raw-flesh",
    "prolonged-contact",
    "intimate-contact",
]

CONTACT_MODIFIERS: dict[ContactKind, int] = {
    "avoided": 4,
    "shared-building": 3,
    "close-conversation": 2,
    "brief-touch": 1,
    "shared-material": 0,
    "cooked-flesh": 0,
    "raw-flesh": -1,
    "prolonged-contact": -2,
    "intimate-contact": -3,
}


class PermanentAttributeLoss(Record):
    """Exact lasting loss; application belongs to an approved build mutation."""

    st: int = Field(default=0, ge=0, le=20)
    dx: int = Field(default=0, ge=0, le=20)
    iq: int = Field(default=0, ge=0, le=20)
    ht: int = Field(default=0, ge=0, le=20)

    @property
    def empty(self) -> bool:
        return not (self.st or self.dx or self.iq or self.ht)

    def plus(self, other: PermanentAttributeLoss) -> PermanentAttributeLoss:
        return PermanentAttributeLoss(
            st=self.st + other.st,
            dx=self.dx + other.dx,
            iq=self.iq + other.iq,
            ht=self.ht + other.ht,
        )


class PermanentChange(Record):
    id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    cause_id: str = Field(min_length=1)
    losses: PermanentAttributeLoss
    source_ref: str = Field(pattern=r"^B(?:20-21|442-444)$")
    status: Literal["pending", "applied"] = "pending"
    approved_build_revision: str | None = None

    @model_validator(mode="after")
    def valid_boundary(self) -> Self:
        if self.losses.empty:
            raise ValueError("Permanent change requires an attribute loss")
        if (self.status == "applied") != (self.approved_build_revision is not None):
            raise ValueError("Applied permanent changes require an approved build revision")
        return self


class DiseaseProfile(Record):
    """Scenario-authored disease definition; never a player payload."""

    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE_ID
    vector: Literal["blood", "contact", "digestive", "respiratory"]
    resistance_modifier: int = Field(default=0, ge=-6, le=0)
    incubation_seconds: int = Field(default=86_400, ge=1, le=31_536_000)
    cycle_seconds: int = Field(default=86_400, ge=1, le=31_536_000)
    cycles: int = Field(default=1, ge=1, le=1000)
    damage_dice: int = Field(default=0, ge=0, le=1)
    damage_add: int = Field(default=1, ge=1, le=6)
    symptom_hp_numerator: int = Field(default=1, ge=1, le=3)
    symptom_hp_denominator: Literal[1, 2, 3] = 3
    symptom_effect_ids: tuple[str, ...] = ()
    bacterial: bool = False
    drug_resistant: bool = False
    acquired_immunity: bool = False
    lasting_after_damage: int | None = Field(default=None, ge=1)
    lasting_loss: PermanentAttributeLoss = PermanentAttributeLoss()
    source_ref: Literal["B442-444"] = "B442-444"

    @model_validator(mode="after")
    def valid_profile(self) -> Self:
        if self.symptom_hp_numerator > self.symptom_hp_denominator:
            raise ValueError("Symptom threshold cannot exceed full HP")
        if self.damage_dice and self.damage_add != 1:
            raise ValueError("Disease damage is either a fixed amount or 1d")
        if (self.lasting_after_damage is None) != self.lasting_loss.empty:
            raise ValueError("Lasting threshold and loss must be authored together")
        if len(set(self.symptom_effect_ids)) != len(self.symptom_effect_ids):
            raise ValueError("Disease symptom effect IDs must be unique")
        return self


class ContactExposure(Record):
    """One trusted contact relationship; proximity alone is never exposure."""

    id: str = Field(min_length=1)
    actor_id: str = Field(min_length=1)
    disease_id: str = Field(min_length=1)
    vector: Literal["blood", "contact", "digestive", "respiratory"]
    contact: ContactKind
    occurred_at: int = Field(ge=0)
    check_after_seconds: int = Field(default=86_400, ge=1, le=604_800)
    carrier_id: str | None = None
    protection_id: str | None = None
    protection_bonus: int = Field(default=0, ge=0, le=10)
    protection_understood: bool = False

    @model_validator(mode="after")
    def valid_protection(self) -> Self:
        if self.protection_bonus and (not self.protection_id or not self.protection_understood):
            raise ValueError("Protection bonuses require an understood authored precaution")
        if self.protection_id and not self.protection_understood:
            raise ValueError("Ununderstood precautions cannot provide protection")
        return self


class DiseaseEpisode(Record):
    id: str
    actor_id: str
    relationship_id: str
    profile: DiseaseProfile
    started: int = Field(ge=0)
    due: int = Field(ge=0)
    stage: Literal["exposure", "incubating", "cycles", "recovered", "resisted"]
    active: bool = True
    remaining: int = Field(ge=0)
    ht: int = Field(ge=1)
    full_hp: int = Field(ge=1)
    contact_modifier: int = Field(ge=-5, le=4)
    protection_bonus: int = Field(default=0, ge=0, le=10)
    treatment_bonus: int = Field(default=0, ge=0, le=20)
    total_damage: int = Field(default=0, ge=0)
    symptomatic: bool = False
    diagnosed: bool = False
    immune: bool = False
    checks: tuple[CheckTrace, ...] = ()
    private_carrier_id: str | None = None
    permanent_change_id: str | None = None


class WoundInfectionRisk(Record):
    id: str
    actor_id: str
    wound_event_id: str
    opened_at: int = Field(ge=0)
    due: int = Field(ge=0)
    contamination_modifier: int = Field(default=0, ge=-5, le=0)
    antibiotics: bool = False
    treatment_critical_failure: bool = False
    settled: bool = False
    infection_episode_id: str | None = None

    @model_validator(mode="after")
    def valid_timeline(self) -> Self:
        if self.due < self.opened_at:
            raise ValueError("Infection check cannot precede the wound")
        return self


class AgingRules(Record):
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE_ID
    enabled: bool = False
    technology_level: int = Field(default=3, ge=0, le=12)
    extended_lifespan_levels: int = Field(default=0, ge=0, le=10)
    short_lifespan_levels: int = Field(default=0, ge=0, le=10)
    unaging: bool = False
    longevity: bool = False
    consequence_mode: Literal["attributes"] = "attributes"
    source_ref: Literal["B20-21", "B444"] = "B444"


class AgingSchedule(Record):
    id: str
    actor_id: str
    started: int = Field(ge=0)
    age_seconds_at_start: int = Field(ge=0)
    due: int = Field(ge=0)
    rules: AgingRules
    ht: int = Field(ge=1)
    fitness_modifier: int = Field(default=0, ge=-2, le=2)
    checks: tuple[CheckTrace, ...] = ()
    pending_change_ids: tuple[str, ...] = ()
