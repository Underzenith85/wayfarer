"""Versioned command/event records shared by persistence adapters."""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer import validation
from wayfarer.models import Campaign, Event, Record
from wayfarer.persistence.upcasters import UpcasterRegistry
from wayfarer.rules.randomness import RNG_ALGORITHM
from wayfarer.simulation import ENGINE_VERSION
from wayfarer.simulation.events import EngineEvent

EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CommandEntropy:
    seed: str = field(repr=False)
    engine_version: str = ENGINE_VERSION
    rng_algorithm: str = RNG_ALGORITHM


def payload_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


class CommandOrigin(Record):
    """Private validated proposal annotation, never an engine input."""

    schema_version: Literal[1] = 1
    proposal_type: str = Field(min_length=1, max_length=100)
    proposal_json: str = Field(max_length=32000, repr=False)
    provider: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_digest(self) -> CommandOrigin:
        if payload_digest(json.loads(self.proposal_json)) != self.digest:
            raise ValueError("Origin proposal digest mismatch")
        return self

    @classmethod
    def proposal(
        cls, kind: str, proposal: object, *, provider: str, model: str | None = None
    ) -> CommandOrigin:
        return cls(
            proposal_type=kind,
            proposal_json=json.dumps(proposal, sort_keys=True, separators=(",", ":")),
            provider=provider,
            model=model,
            digest=payload_digest(proposal),
        )


@dataclass(frozen=True, slots=True)
class CommandRecord:
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
    recorded_at_us: int | None = None
    origin: CommandOrigin | None = field(default=None, repr=False)
    command_input: str | None = field(default=None, repr=False)

    @property
    def reexecutable(self) -> bool:
        """Legacy, injected-source and different-engine rows are explicitly excluded."""
        return (
            self.entropy_seed is not None
            and self.engine_version == ENGINE_VERSION
            and self.rng_algorithm == RNG_ALGORITHM
        )


@dataclass(frozen=True, slots=True)
class StoredEvent:
    campaign_id: str
    command_id: str
    revision: int
    ordinal: int
    event: EngineEvent
    schema_version: int = EVENT_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class CommandResolution:
    transcript: Event
    events: list[EngineEvent]


COMMAND_UPCASTERS = UpcasterRegistry({"command": 1}, {})
_COMMAND_ADAPTER = TypeAdapter(CommandRecord)


def upcast_command(record: CommandRecord) -> CommandRecord:
    raw = validation.mapping(validation.decode(_COMMAND_ADAPTER.dump_json(record).decode()))
    row = COMMAND_UPCASTERS.read("command", record.schema_version, raw)
    row["schema_version"] = COMMAND_UPCASTERS.current["command"]
    return _COMMAND_ADAPTER.validate_json(json.dumps(row))
