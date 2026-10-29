"""Durable exact-origin battle exertion ledger; no inference from world time."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, field_validator, model_validator

from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "battle-fatigue:"


class BattleFatigueLedger(Record):
    generation: Literal[1] = 1
    encounter_id: Id
    binding: str = Field(pattern=r"^[0-9a-f]{64}$")
    initial_round: int = Field(ge=1)
    initial_index: int = Field(ge=0)
    round: int = Field(ge=1)
    index: int = Field(ge=0)
    seconds: int = Field(ge=0, le=119)
    roster: tuple[Id, ...]
    rolled: tuple[Id, ...] = ()
    charged: bool = False
    partial_second: bool = False

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported battle fatigue generation")
        return value

    @model_validator(mode="after")
    def coherent(self) -> BattleFatigueLedger:
        if (
            not self.roster
            or len(set(self.roster)) != len(self.roster)
            or self.rolled != tuple(sorted(set(self.rolled)))
            or not set(self.rolled) <= set(self.roster)
            or self.round < self.initial_round
            or self.seconds != self.round - self.initial_round + int(self.partial_second)
            or self.partial_second
            and not self.charged
            or self.initial_index >= len(self.roster)
            or self.index >= len(self.roster)
        ):
            raise ValueError("Invalid battle fatigue ledger")
        return self


def identifier(encounter_id: str) -> str:
    return PREFIX + hashlib.sha256(encounter_id.encode()).hexdigest()


def read(resources: ResourceState, encounter_id: str) -> BattleFatigueLedger:
    matches = tuple(e for e in resources.events if e.id == identifier(encounter_id))
    if len(matches) != 1:
        raise ValidationError("Missing or duplicated battle fatigue ledger")
    event = matches[0]
    try:
        ledger = BattleFatigueLedger.model_validate_json(event.kind)
    except ValueError as exc:
        raise ValidationError("Corrupt battle fatigue ledger") from exc
    if (
        ledger.encounter_id != encounter_id
        or event.target_id != encounter_id
        or event.at > resources.game_time
    ):
        raise ValidationError("Battle fatigue ledger origin mismatch")
    return ledger


def write(resources: ResourceState, ledger: BattleFatigueLedger) -> ResourceState:
    key = identifier(ledger.encounter_id)
    old = next((e for e in resources.events if e.id == key), None)
    event = ResourceEvent(
        id=key,
        at=old.at if old else resources.game_time,
        target_id=ledger.encounter_id,
        kind=ledger.model_dump_json(),
    )
    events = tuple(event if e.id == key else e for e in resources.events)
    return resources.model_copy(update={"events": events if old else events + (event,)})
