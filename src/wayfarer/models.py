"""Shared typed contracts: the entity base class and the legacy demo records.

Entities are frozen, strict, closed records. Every state transition returns a new
record; verbs live in engines and functions, never on the entity itself.
Runtime validation remains at the service boundary.
"""

from __future__ import annotations

from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """Immutable entity contract shared by rules, character, simulation and orchestration."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


Id = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=1, le=1000000)]
Tick = Annotated[int, Field(ge=0)]

Action = Literal["observe", "talk", "sneak", "rest", "ask"]


class Character(TypedDict):
    name: str
    concept: str
    attributes: dict[str, int]
    skills: dict[str, int]
    traits: list[str]


class Roll(TypedDict):
    dice: list[int]
    total: int
    target: int
    success: bool
    critical: Literal["success", "failure"] | None


class Message(TypedDict):
    role: str
    text: str
    roll: NotRequired[Roll | None]
    action: NotRequired[Action]
    flavor: NotRequired[str]


class Campaign(TypedDict):
    id: str
    revision: int
    rules: str
    rules_ref: NotRequired[RulesReference]
    resources_json: NotRequired[str]
    play_json: NotRequired[str]
    setup_json: NotRequired[str]
    scenario_document_json: NotRequired[str]
    scenario_graph_json: NotRequired[str]
    character: Character
    scenario: dict[str, str]
    hp: int
    fp: int
    minutes: int
    location: str
    inventory: list[str]
    discoveries: list[str]
    flags: list[str]
    complete: bool
    messages: list[Message]


class RulesPackagePin(TypedDict):
    id: str
    version: str
    digest: str


class RulesReference(TypedDict):
    edition: str
    packages: list[RulesPackagePin]
    policy_id: str
    policy_version: int


EventAction = (
    Action
    | Literal[
        "resource",
        "v1-membership",
        "typed-action",
        "power-approval",
        "request_ruling",
        "decide_ruling",
        "execute_ruling",
        "evaluate_ruling",
        "combat",
        "advancement",
        "rules-migration",
        "encounter-scenes",
        "scene",
        "objectives",
        "noncombat",
        "party",
        "npc",
        "recovery",
        "director",
        "workshop",
        "setup",
    ]
)


class Event(TypedDict):
    input: str
    action: EventAction
    outcome: str
    roll: Roll | None


class ValidationResult(TypedDict):
    valid: bool
    errors: list[str]
    spent: int
    remaining: int
    levels: dict[str, int]


class PublicCampaign(Campaign):
    validation: ValidationResult


class CommittedTurn(TypedDict):
    kind: Literal["committed"]
    state: Campaign
    event: Event


class ReplayedTurn(TypedDict):
    kind: Literal["replayed"]
    state: Campaign


type TurnResult = CommittedTurn | ReplayedTurn
