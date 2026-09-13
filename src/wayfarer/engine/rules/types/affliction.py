"""Persisted Basic Set affliction facts used by the B416 resolver."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.models import Record

AfflictionCondition = Literal[
    "agony",
    "attribute-penalty",
    "blindness",
    "choking",
    "coma",
    "coughing",
    "daze",
    "drowsy",
    "drunk",
    "ecstasy",
    "euphoria",
    "hallucinating",
    "nauseated",
    "pain",
    "paralysis",
    "retching",
    "seizure",
    "sleep",
    "stun",
    "tipsy",
    "unconsciousness",
]

PenetrationModifier = Literal[
    "ordinary",
    "armor-divisor",
    "blood-agent",
    "contact-agent",
    "respiratory-agent",
    "sense-based",
    "follow-up",
]

AfflictionDelivery = Literal["direct", "linked", "side-effect"]


class AfflictionEffect(Record):
    """One typed, historical effect; activity is owned by ``active_effect_ids``."""

    id: str
    actor_id: str
    source_id: str
    condition: AfflictionCondition
    started_at: int = Field(ge=0)
    expires_at: int = Field(ge=1)
    level: int = Field(default=1, ge=1)
