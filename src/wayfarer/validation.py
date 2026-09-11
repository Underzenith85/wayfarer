"""Small runtime schemas for the preserved demo; no unchecked casts at boundaries.

These validate structure and primitive types, not campaign legality. The character
builder owns costs and campaign policy. JSON decoding itself returns object.
"""

import json

from wayfarer.models import (
    Action,
    Campaign,
    Character,
    EventAction,
    Message,
    Roll,
    RulesPackagePin,
    RulesReference,
)


def decode(raw: str | bytes) -> object:
    value: object = json.loads(raw)
    return value


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("Expected an object with string keys")
    return {k: v for k, v in value.items()}


def sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return list(value)


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("Expected an integer (not boolean)")
    return value


def boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean")
    return value


def strings(value: object) -> list[str]:
    return [string(v) for v in sequence(value)]


def integers(value: object) -> dict[str, int]:
    return {k: integer(v) for k, v in mapping(value).items()}


def fields(data: dict[str, object], required: set[str], optional: set[str] | None = None) -> None:
    if not required <= data.keys() or data.keys() - required - (optional or set()):
        raise ValueError("Missing or unknown fields")


def character(value: object) -> Character:
    d = mapping(value)
    fields(d, {"name", "concept", "attributes", "skills", "traits"})
    attributes = integers(d["attributes"])
    if set(attributes) != {"ST", "DX", "IQ", "HT"}:
        raise ValueError("Exactly ST, DX, IQ and HT are required")
    return Character(
        name=string(d["name"]),
        concept=string(d["concept"]),
        attributes=attributes,
        skills=integers(d["skills"]),
        traits=strings(d["traits"]),
    )


def scenario(value: object) -> dict[str, str]:
    d = mapping(value)
    fields(d, {"title", "premise", "location", "contact", "clue", "secret", "objective"})
    result = {k: string(v) for k, v in d.items()}
    if any(not 1 <= len(v) <= 2000 for v in result.values()):
        raise ValueError("Scenario fields must contain 1–2000 characters")
    return result


def action(value: object) -> Action:
    match value:
        case "observe" | "talk" | "sneak" | "rest" | "ask":
            return value
        case _:
            raise ValueError("Unsupported action proposal")


def roll(value: object) -> Roll:
    d = mapping(value)
    fields(d, {"dice", "total", "target", "success", "critical"})
    dice = [integer(v) for v in sequence(d["dice"])]
    total = integer(d["total"])
    if len(dice) != 3 or any(not 1 <= die <= 6 for die in dice) or sum(dice) != total:
        raise ValueError("Invalid roll data")
    critical = d["critical"]
    if critical is None:
        result: Roll = {
            "dice": dice,
            "total": total,
            "target": integer(d["target"]),
            "success": boolean(d["success"]),
            "critical": None,
        }
    elif critical == "success":
        result = {
            "dice": dice,
            "total": total,
            "target": integer(d["target"]),
            "success": boolean(d["success"]),
            "critical": "success",
        }
    elif critical == "failure":
        result = {
            "dice": dice,
            "total": total,
            "target": integer(d["target"]),
            "success": boolean(d["success"]),
            "critical": "failure",
        }
    else:
        raise ValueError("Invalid critical result")
    return result


def message(value: object) -> Message:
    d = mapping(value)
    fields(d, {"role", "text"}, {"roll", "action", "flavor"})
    result: Message = {"role": string(d["role"]), "text": string(d["text"])}
    if "roll" in d:
        result["roll"] = None if d["roll"] is None else roll(d["roll"])
    if "action" in d:
        result["action"] = action(d["action"])
    if "flavor" in d:
        result["flavor"] = string(d["flavor"])
    return result


def campaign(value: object) -> Campaign:
    d = mapping(value)
    fields(
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
        id=string(d["id"]),
        revision=integer(d["revision"]),
        rules=string(d["rules"]),
        character=character(d["character"]),
        scenario=scenario(d["scenario"]),
        hp=integer(d["hp"]),
        fp=integer(d["fp"]),
        minutes=integer(d["minutes"]),
        location=string(d["location"]),
        inventory=strings(d["inventory"]),
        discoveries=strings(d["discoveries"]),
        flags=strings(d["flags"]),
        complete=boolean(d["complete"]),
        messages=[message(m) for m in sequence(d["messages"])],
    )
    if "setup_json" in d:
        result["setup_json"] = string(d["setup_json"])
    if "scenario_document_json" in d:
        result["scenario_document_json"] = string(d["scenario_document_json"])
    if "scenario_reference_json" in d:
        result["scenario_reference_json"] = string(d["scenario_reference_json"])
    if "combat_rules_json" in d:
        result["combat_rules_json"] = string(d["combat_rules_json"])
    if "scenario_graph_json" in d:
        result["scenario_graph_json"] = string(d["scenario_graph_json"])
    if "play_json" in d:
        result["play_json"] = string(d["play_json"])
    if "resources_json" in d:
        result["resources_json"] = string(d["resources_json"])
    if "rules_ref" in d:
        result["rules_ref"] = rules_reference(d["rules_ref"])
    return result


def rules_reference(value: object) -> RulesReference:
    data = mapping(value)
    fields(data, {"edition", "packages", "policy_id", "policy_version"})
    pins: list[RulesPackagePin] = []
    for value_pin in sequence(data["packages"]):
        pin = mapping(value_pin)
        fields(pin, {"id", "version", "digest"})
        digest = string(pin["digest"])
        if len(digest) != 64:
            raise ValueError("Invalid rules package digest")
        pins.append(
            RulesPackagePin(id=string(pin["id"]), version=string(pin["version"]), digest=digest)
        )
    return RulesReference(
        edition=string(data["edition"]),
        packages=pins,
        policy_id=string(data["policy_id"]),
        policy_version=integer(data["policy_version"]),
    )


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
        case _:
            return action(value)
