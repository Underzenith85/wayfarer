"""Persistent toxin, intoxication, and dependency records (Campaigns B437-B441)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError
from wayfarer.models import Record

DeliveryVector = Literal["contact", "blood", "digestive", "respiratory", "sense", "follow-up"]


class ToxinProfile(Record):
    """Trusted scenario profile.  Descriptive identity is never accepted from a player."""

    id: str = Field(min_length=1, max_length=120)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    vector: DeliveryVector
    delay: int = Field(default=0, ge=0, le=31536000)
    interval: int = Field(default=1, ge=1, le=31536000)
    cycles: int = Field(default=1, ge=1, le=100000)
    resistance_modifier: int = Field(default=0, ge=-30, le=30)
    resistible: bool = True
    hp_dice: int = Field(default=0, ge=0, le=100)
    hp_add: int = Field(default=0, ge=0, le=1000)
    fp_dice: int = Field(default=0, ge=0, le=100)
    fp_add: int = Field(default=0, ge=0, le=1000)
    condition: Literal[
        "none",
        "coughing",
        "blindness",
        "drowsy",
        "ecstasy",
        "hallucinating",
        "unconscious",
        "paralysis",
        "retching",
        "seizure",
    ] = "none"
    condition_seconds: int = Field(default=0, ge=0)
    duration_per_margin: int = Field(default=0, ge=0)
    treatment_owner: Literal["none", "first-aid", "physician", "poisons", "antidote"] = "none"
    addictive: Literal["none", "physiological", "psychological"] = "none"
    depressant: bool = False
    reference: str = Field(pattern=r"^B(437|438|439|440|441)(-|$)")


class DeliveryEvidence(Record):
    """Trusted facts about the attack/exposure; unsupported shortcuts fail closed."""

    touched_skin: bool = False
    mucous_or_open_wound: bool = False
    swallowed: bool = False
    inhaled: bool = False
    relevant_sense: bool = False
    penetrated_damage: bool = False
    sealed: bool = False
    filter_lungs: bool = False
    protected_sense: bool = False
    skin_covered: bool = False


class ToxinExposure(Record):
    id: str
    actor_id: str
    profile: ToxinProfile
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    started: int = Field(ge=0)
    due: int = Field(ge=0)
    remaining: int = Field(ge=0)
    ht: int = Field(ge=1)
    dose: int = Field(default=1, ge=1, le=1024)
    treatment_bonus: int = Field(default=0, ge=0, le=30)
    discovered_by: tuple[str, ...] = ()
    active: bool = True
    cycle: int = Field(default=0, ge=0)
    hp_lost: int = Field(default=0, ge=0)
    fp_lost: int = Field(default=0, ge=0)
    condition_until: int = Field(default=0, ge=0)
    overdose_until: int = Field(default=0, ge=0)
    halted_by: str | None = None

    @model_validator(mode="after")
    def timeline(self) -> ToxinExposure:
        if self.due < self.started or (self.active and self.remaining == 0):
            raise ValueError("Invalid toxin timeline")
        return self


class ToxinView(Record):
    id: str
    actor_id: str
    substance_id: str | None = None
    active: bool
    due: int
    symptoms_visible: bool


class Intoxication(Record):
    actor_id: str
    window_started: int = Field(ge=0)
    drinks: int = Field(default=0, ge=0)
    total_session_drinks: int = Field(default=0, ge=0)
    level: Literal["sober", "tipsy", "drunk", "unconscious", "coma"] = "sober"
    stopped_at: int | None = Field(default=None, ge=0)
    sober_due: int | None = Field(default=None, ge=0)
    hangover_due: int | None = Field(default=None, ge=0)
    hangover_until: int = Field(default=0, ge=0)


class DrugDependency(Record):
    id: str
    actor_id: str
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["physiological", "psychological"]
    started: int = Field(ge=0)
    due: int = Field(ge=0)
    successes: int = Field(default=0, ge=0, le=14)
    quirks: int = Field(default=0, ge=0)
    active: bool = True
    discovered_by: tuple[str, ...] = ()


def require_toxins_settled(
    toxins: tuple[ToxinExposure, ...],
    dependencies: tuple[DrugDependency, ...],
    actors: frozenset[str],
    at: int,
) -> None:
    if any(t.active and t.actor_id in actors and t.due <= at for t in toxins) or any(
        d.active and d.actor_id in actors and d.due <= at for d in dependencies
    ):
        raise ConflictError("Resolve due toxin or withdrawal consequences before further activity")
