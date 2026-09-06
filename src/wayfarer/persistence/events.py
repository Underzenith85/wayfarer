"""Versioned command/event records shared by persistence adapters."""

import hashlib
import json
from dataclasses import dataclass

from wayfarer.models import Campaign, Event

EVENT_SCHEMA_VERSION = 1


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
