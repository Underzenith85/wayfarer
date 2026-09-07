"""Durable lobby contracts. Invitations are bound to authenticated principals."""

from typing import Literal

from pydantic import Field

from wayfarer.simulation.continuation import AdventureSnapshot
from wayfarer.simulation.profiles import ProfileSelection
from wayfarer.simulation.resources import Id, Record
from wayfarer.simulation.studio import GenerationBrief, ScenarioGraph

Phase = Literal["draft", "ready", "active", "paused", "completed", "archived"]


class Seat(Record):
    principal_id: Id
    joined: bool = False
    actor_ids: tuple[Id, ...] = ()
    ready: bool = False


class Setup(Record):
    host_id: Id
    creation_json: str
    brief: GenerationBrief
    phase: Phase = "draft"
    seats: tuple[Seat, ...]
    graph: ScenarioGraph | None = None
    adventures: tuple[AdventureSnapshot, ...] = ()
    next_graph: ScenarioGraph | None = None


class CreateSetup(Record):
    id: Id
    brief: GenerationBrief
    graph: ScenarioGraph | None = None
    # Omitted means the server default profile; the saved pin never floats afterwards.
    rules_profile: ProfileSelection | None = None


class SetupCommand(Record):
    id: Id
    expected_revision: int = Field(ge=0)
    operation: Literal[
        "edit",
        "invite",
        "join",
        "assign",
        "ready",
        "activate",
        "pause",
        "resume",
        "complete",
        "archive",
        "unarchive",
        "preview",
        "continue",
    ]
    brief: GenerationBrief | None = None
    graph: ScenarioGraph | None = None
    principal_id: Id | None = None
    actor_ids: tuple[Id, ...] = ()
    ready: bool = True
