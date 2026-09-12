"""Player-facing tactical records; the class names are the frozen tactical-v1 schema."""

from __future__ import annotations

from pydantic import Field

from wayfarer.engine.simulation.combat.tactical import TacticalTrace
from wayfarer.engine.simulation.hex_geometry import Cell, Hex
from wayfarer.models import Record
from wayfarer.orchestration.combat import (
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
)


class TacticalActor(Record):
    id: str
    name: str
    position: Hex
    facing: int
    posture: str
    controlled: bool
    grappled: bool
    pinned: bool


class TacticalGrip(Record):
    id: str
    holder_id: str
    target_id: str
    location: str
    hands: tuple[str, ...]


class TacticalChoice(Record):
    label: str
    command: TakeCombatTurn | TakeUnarmedTurn | ChooseDefense | ResumeInterruptedTurn


class TacticalEncounter(Record):
    id: str
    status: str
    round: int
    current_actor_id: str | None
    coordinate_system: str = "hex-axial-v1"
    cells: tuple[Cell, ...]
    actors: tuple[TacticalActor, ...]
    grips: tuple[TacticalGrip, ...]
    choices: tuple[TacticalChoice, ...]
    notice: str | None = None
    traces: tuple[TacticalTrace, ...] = ()


class TacticalSnapshot(Record):
    version: str = "tactical-v1"
    campaign_id: str
    actor_id: str
    revision: int = Field(ge=0)
    encounters: tuple[TacticalEncounter, ...]
