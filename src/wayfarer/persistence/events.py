"""Versioned command/event records shared by persistence adapters."""

import hashlib
import json
from dataclasses import dataclass, field

from wayfarer.models import Campaign, Event
from wayfarer.rules.randomness import RNG_ALGORITHM
from wayfarer.simulation import ENGINE_VERSION

EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CommandEntropy:
    seed: str = field(repr=False)
    engine_version: str = ENGINE_VERSION
    rng_algorithm: str = RNG_ALGORITHM


def payload_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredEvent:
    campaign_id: str
    command_id: str
    actor_id: str
    expected_revision: int
    resulting_revision: int
    payload_hash: str
    rules_version: str
    event: Event
    state_after: Campaign
    schema_version: int = EVENT_SCHEMA_VERSION
    entropy_seed: str | None = field(default=None, repr=False)
    engine_version: str | None = None
    rng_algorithm: str | None = None

    @property
    def reexecutable(self) -> bool:
        """Legacy, injected-source and different-engine rows are explicitly excluded."""
        return (
            self.entropy_seed is not None
            and self.engine_version == ENGINE_VERSION
            and self.rng_algorithm == RNG_ALGORITHM
        )
