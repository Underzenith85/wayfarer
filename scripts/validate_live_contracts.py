"""Offline live schemas and bounded consumer recovery traces; not runtime transport."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator, FormatChecker

from scripts.validate_contracts import ROOT, mapping, nodes, read, resolve, sequence


def live_schema() -> dict[str, object]:
    document = deepcopy(mapping(read(ROOT / "events.schema.json")))
    shared = mapping(read(ROOT / "schemas.json"))
    definitions = mapping(document["$defs"])
    for name, value in mapping(shared["$defs"]).items():
        copied = deepcopy(value)
        for node in nodes(copied):
            if "$ref" in node:
                reference = str(node["$ref"])
                if not reference.startswith("#/$defs/"):
                    raise ValueError(f"Unexpected shared reference: {reference}")
                node["$ref"] = "#/$defs/HTTP_" + reference[len("#/$defs/") :]
        definitions["HTTP_" + name] = copied
    for node in nodes(document):
        if "$ref" in node:
            reference = str(node["$ref"])
            if reference.startswith("./schemas.json#/$defs/"):
                reference = "#/$defs/HTTP_" + reference[len("./schemas.json#/$defs/") :]
                node["$ref"] = reference
            resolve(document, node)
    Draft202012Validator.check_schema(document)
    return document


def message_validator(document: dict[str, object], direction: str) -> Draft202012Validator:
    if direction not in {"client", "server"}:
        raise ValueError("Unknown message direction")
    schema = {key: value for key, value in document.items() if key != "oneOf"}
    schema["$ref"] = "#/$defs/" + direction.title() + "Message"
    return Draft202012Validator(schema, format_checker=FormatChecker())


def resource_key(resource: dict[str, object]) -> str:
    value = mapping(resource["value"])
    return str(resource["kind"]) + ":" + str(value.get("id", value.get("actor_id")))


@dataclass
class Narration:
    voice_session: object
    chunks: list[str] = field(default_factory=list)
    segments: dict[int, dict[str, object]] = field(default_factory=dict)


@dataclass
class Consumer:
    """Oracle for admitted subscription fixtures, not authentication or server authorization."""

    subscription_id: str
    scope: dict[str, object]
    epoch: str
    cursor: str | None
    resources: dict[str, dict[str, object]] = field(default_factory=dict)
    active: bool = True
    cache_valid: bool = False
    dirty: set[str] = field(default_factory=set)
    seen: dict[str, dict[str, object]] = field(default_factory=dict)
    narrations: dict[str, Narration] = field(default_factory=dict)
    staged: list[dict[str, object]] | None = None
    begin: dict[str, object] | None = None

    @classmethod
    def from_fixture(cls, initial: dict[str, object]) -> Consumer:
        cursor = initial["cursor"]
        consumer = cls(
            str(initial["subscription_id"]),
            mapping(initial["scope"]),
            str(initial["visibility_epoch"]),
            None if cursor is None else str(cursor),
        )
        for resource in sequence(initial["resources"]):
            item = mapping(resource)
            consumer.check_resource(item)
            consumer.resources[resource_key(item)] = mapping(item["value"])
        consumer.cache_valid = cursor is not None
        return consumer

    def check_resource(self, resource: dict[str, object]) -> None:
        value = mapping(resource["value"])
        kind = resource["kind"]
        expected = {
            "campaign": ("id", "campaign_id"),
            "scene": ("id", "scene_id"),
            "character": ("id", "actor_id"),
            "inventory": ("actor_id", "actor_id"),
            "action": ("actor_id", "actor_id"),
        }
        key, scope_key = expected[str(kind)]
        if value[key] != self.scope[scope_key]:
            raise ValueError("Snapshot resource belongs to another scope")
        if kind == "action" and value["scene_id"] != self.scope["scene_id"]:
            raise ValueError("Action belongs to another scene")
        if kind == "character" and value["campaign_id"] != self.scope["campaign_id"]:
            raise ValueError("Character belongs to another campaign")

    def purge(self) -> str:
        self.active = False
        self.cursor = None
        self.cache_valid = False
        self.resources.clear()
        self.dirty.clear()
        self.narrations.clear()
        self.seen.clear()
        self.staged = None
        self.begin = None
        return "purged"

    def resync(self) -> str:
        self.active = False
        self.narrations.clear()
        self.staged = None
        self.begin = None
        return "resync"

    def accept(self, message: dict[str, object]) -> str:
        if message.get("subscription_id") != self.subscription_id or not self.active:
            return "ignored"
        if "scope" in message and (
            message["scope"] != self.scope or message["visibility_epoch"] != self.epoch
        ):
            return "ignored"
        kind = str(message["type"])
        if kind in {"stream.reset", "subscription.revoked", "unsubscribed"}:
            return self.purge()
        if kind == "snapshot.begin":
            if self.staged is not None:
                raise ValueError("Overlapping snapshots")
            self.begin = message
            self.staged = []
            return "staged"
        if kind in {"snapshot.resource", "snapshot.end"}:
            if self.begin is None or self.staged is None:
                raise ValueError("Snapshot frame without begin")
            if message["snapshot_id"] != self.begin["snapshot_id"]:
                raise ValueError("Mismatched snapshot identity")
            if kind == "snapshot.resource":
                if message["index"] != len(self.staged):
                    raise ValueError("Noncontiguous snapshot resource")
                item = mapping(message["resource"])
                self.check_resource(item)
                if resource_key(item) in {resource_key(value) for value in self.staged}:
                    raise ValueError("Duplicate snapshot resource")
                self.staged.append(item)
                return "staged"
            if (
                message["cursor"] != self.begin["cursor"]
                or message["resource_count"] != self.begin["resource_count"]
                or message["resource_count"] != len(self.staged)
            ):
                raise ValueError("Incomplete or inconsistent snapshot")
            kinds = [item["kind"] for item in self.staged]
            if any(
                kinds.count(required) != 1
                for required in ("campaign", "scene", "character", "inventory")
            ):
                raise ValueError("Missing required snapshot resource")
            self.resources = {resource_key(item): mapping(item["value"]) for item in self.staged}
            self.cursor = str(message["cursor"])
            self.cache_valid = True
            self.dirty.clear()
            self.seen.clear()
            self.staged = None
            self.begin = None
            return "applied"
        if kind == "stream.ready":
            if self.staged is not None or not self.cache_valid or message["cursor"] != self.cursor:
                raise ValueError("Ready does not match applied checkpoint")
            return "ready"
        if kind in {"action.updated", "projection.invalidated"}:
            if self.staged is not None or not self.cache_valid:
                raise ValueError("Durable event before snapshot publication")
            eid = str(message["event_id"])
            if eid in self.seen:
                return "ignored" if self.seen[eid] == message else self.resync()
            if message["previous_cursor"] != self.cursor or message["cursor"] == self.cursor:
                return self.resync()
            if len(self.seen) >= 10000:
                return self.resync()
            if kind == "action.updated":
                action = mapping(message["action"])
                self.check_resource({"kind": "action", "value": action})
                old = self.resources.get("action:" + str(action["id"]))
                if (
                    old
                    and old["status"] in {"succeeded", "rejected", "cancelled"}
                    and old != action
                ):
                    raise ValueError("Terminal gameplay outcome changed")
                self.resources["action:" + str(action["id"])] = action
                changes = (
                    sequence(mapping(action["resolution"])["changed_resources"])
                    if action["status"] == "succeeded"
                    else []
                )
            else:
                changes = sequence(message["resources"])
            for change in changes:
                value = mapping(change)
                self.dirty.add(str(value["resource_type"]) + ":" + str(value["resource_id"]))
            self.seen[eid] = message
            self.cursor = str(message["cursor"])
            return "applied"
        if kind.startswith("narration.") or kind == "voice.segment":
            return self.narration(message)
        raise ValueError(f"Unsupported trace message: {kind}")

    def narration(self, message: dict[str, object]) -> str:
        kind = str(message["type"])
        nid = str(message["narration_id"])
        if kind == "narration.started":
            action = self.resources.get("action:" + str(message["action_id"]))
            if message["basis"] == "committed" and (
                action is None or action["status"] != "succeeded"
            ):
                raise ValueError("Committed narration without a committed action")
            if action and action["command_id"] != message["command_id"]:
                raise ValueError("Narration command mismatch")
            if nid in self.narrations:
                raise ValueError("Overlapping narration identity")
            self.narrations[nid] = Narration(message["voice_session_id"])
            return "narration"
        stream = self.narrations.get(nid)
        if stream is None:
            return "ignored"
        if kind == "narration.delta":
            index = int(str(message["index"]))
            if index < len(stream.chunks) and stream.chunks[index] == message["text"]:
                return "ignored"
            if index != len(stream.chunks):
                del self.narrations[nid]
                return "narration_stopped"
            stream.chunks.append(str(message["text"]))
        elif kind == "voice.segment":
            if stream.voice_session is None or message["voice_session_id"] != stream.voice_session:
                raise ValueError("Voice session crossed narration scope")
            index = int(str(message["segment_index"]))
            if index in stream.segments and stream.segments[index] == message:
                return "ignored"
            if index != len(stream.segments):
                del self.narrations[nid]
                return "narration_stopped"
            stream.segments[index] = message
        elif kind == "narration.ended":
            if message["chunk_count"] != len(stream.chunks):
                del self.narrations[nid]
                return "narration_stopped"
            del self.narrations[nid]
        return "narration"

    def summary(self) -> dict[str, object]:
        return {
            "cursor": self.cursor,
            "cache_valid": self.cache_valid,
            "active": self.active,
            "action_statuses": {
                key.removeprefix("action:"): value["status"]
                for key, value in self.resources.items()
                if key.startswith("action:")
            },
            "dirty_resources": sorted(self.dirty),
            "active_narrations": len(self.narrations),
        }


def validate_live() -> tuple[int, int]:
    schema = live_schema()
    validators = {
        direction: message_validator(schema, direction) for direction in ("client", "server")
    }
    examples = sequence(read(ROOT / "events.examples.json"))
    covered: dict[str, set[str]] = {"client": set(), "server": set()}
    for item in examples:
        example = mapping(item)
        direction = str(example["direction"])
        validators[direction].validate(example["message"])
        covered[direction].add(str(mapping(example["message"])["type"]))
    definitions = mapping(schema["$defs"])
    for direction in covered:
        expected = {
            str(mapping(mapping(mapping(definitions[str(name)])["properties"])["type"])["const"])
            for name in sequence(schema[f"x-{direction}-message-types"])
        }
        if covered[direction] != expected:
            raise ValueError(f"Missing message variant examples: {direction}")
    scenarios = sequence(read(ROOT / "events.scenarios.json"))
    for item in scenarios:
        scenario = mapping(item)
        consumer = Consumer.from_fixture(mapping(scenario["initial"]))
        for entry in sequence(scenario["steps"]):
            step = mapping(entry)
            message = mapping(step["message"])
            validators["server"].validate(message)
            # Non-injected fixture publication must match the admitted perspective.
            if not step["injected"] and "scope" in message and message["scope"] != consumer.scope:
                raise ValueError("Fixture server leaked a different viewpoint")
            result = consumer.accept(message)
            if result != step["expect"]:
                raise ValueError(f"{scenario['name']}: expected {step['expect']}, got {result}")
        if consumer.summary() != scenario["expected"]:
            raise ValueError(f"Unexpected final checkpoint: {scenario['name']}")
    return len(examples), len(scenarios)


def main() -> None:
    examples, scenarios = validate_live()
    print(f"Validated {examples} live messages and {scenarios} recovery scenarios (offline)")


if __name__ == "__main__":
    main()
