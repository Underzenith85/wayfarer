"""Application payloads: the campaign envelope, command receipts and turn results.

These are transport and persistence contracts. Orchestration commits them,
persistence stores them and transport serves them; the engine never reads them,
so nothing under ``engine/`` imports this module. The vocabulary they embed
(``Character``, ``Roll``, ``RulesReference``) is the engine's own and lives in
:mod:`wayfarer.models`, where every layer can reach it.

The parsers validate structure and primitive types, not campaign legality.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from wayfarer import validation
from wayfarer.models import Character, Roll, RulesReference, ValidationResult


class Message(TypedDict):
    role: str
    text: str
    roll: NotRequired[Roll | None]
    action: NotRequired[str]
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
    scenario_reference_json: NotRequired[str]
    scenario_graph_json: NotRequired[str]
    combat_rules_json: NotRequired[str]
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


EventAction = Literal[
    "legacy",
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


class CommandReceipt(TypedDict):
    """Command family and typed result; exact input and dice have their own owners."""

    action: EventAction
    outcome: str


class PublicCampaign(Campaign):
    validation: ValidationResult


class CommittedTurn(TypedDict):
    kind: Literal["committed"]
    state: Campaign
    event: CommandReceipt


class ReplayedTurn(TypedDict):
    kind: Literal["replayed"]
    state: Campaign


type TurnResult = CommittedTurn | ReplayedTurn


def message(value: object) -> Message:
    d = validation.mapping(value)
    validation.fields(d, {"role", "text"}, {"roll", "action", "flavor"})
    result: Message = {"role": validation.string(d["role"]), "text": validation.string(d["text"])}
    if "roll" in d:
        result["roll"] = None if d["roll"] is None else validation.roll(d["roll"])
    if "action" in d:
        result["action"] = validation.string(d["action"])
    if "flavor" in d:
        result["flavor"] = validation.string(d["flavor"])
    return result


def campaign(value: object) -> Campaign:
    d = validation.mapping(value)
    validation.fields(
        d,
        {
            "id",
            "revision",
            "rules",
            "character",
            "scenario",
            "hp",
            "fp",
            "minutes",
            "location",
            "inventory",
            "discoveries",
            "flags",
            "complete",
            "messages",
        },
        {
            "rules_ref",
            "resources_json",
            "play_json",
            "scenario_reference_json",
            "scenario_graph_json",
            "combat_rules_json",
            "scenario_document_json",
            "setup_json",
        },
    )
    result = Campaign(
        id=validation.string(d["id"]),
        revision=validation.integer(d["revision"]),
        rules=validation.string(d["rules"]),
        character=validation.character(d["character"]),
        scenario=validation.scenario(d["scenario"]),
        hp=validation.integer(d["hp"]),
        fp=validation.integer(d["fp"]),
        minutes=validation.integer(d["minutes"]),
        location=validation.string(d["location"]),
        inventory=validation.strings(d["inventory"]),
        discoveries=validation.strings(d["discoveries"]),
        flags=validation.strings(d["flags"]),
        complete=validation.boolean(d["complete"]),
        messages=[message(m) for m in validation.sequence(d["messages"])],
    )
    if "setup_json" in d:
        result["setup_json"] = validation.string(d["setup_json"])
    if "scenario_document_json" in d:
        result["scenario_document_json"] = validation.string(d["scenario_document_json"])
    if "scenario_reference_json" in d:
        result["scenario_reference_json"] = validation.string(d["scenario_reference_json"])
    if "combat_rules_json" in d:
        result["combat_rules_json"] = validation.string(d["combat_rules_json"])
    if "scenario_graph_json" in d:
        result["scenario_graph_json"] = validation.string(d["scenario_graph_json"])
    if "play_json" in d:
        result["play_json"] = validation.string(d["play_json"])
    if "resources_json" in d:
        result["resources_json"] = validation.string(d["resources_json"])
    if "rules_ref" in d:
        result["rules_ref"] = validation.rules_reference(d["rules_ref"])
    return result


def event_action(value: object) -> EventAction:
    match value:
        case (
            "resource"
            | "v1-membership"
            | "typed-action"
            | "power-approval"
            | "request_ruling"
            | "decide_ruling"
            | "execute_ruling"
            | "evaluate_ruling"
            | "combat"
            | "advancement"
            | "rules-migration"
            | "scene"
            | "objectives"
            | "noncombat"
            | "party"
            | "npc"
            | "recovery"
            | "director"
            | "workshop"
            | "setup"
        ):
            return value
        case "observe" | "talk" | "sneak" | "rest" | "ask" | "legacy":
            return "legacy"  # Read-only normalization of retained prototype receipts.
        case _:
            raise ValueError("Unsupported command family")
