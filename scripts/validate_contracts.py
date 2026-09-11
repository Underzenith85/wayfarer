"""Validate the reviewed, offline HTTP contract and operation-bound examples."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "v1"


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected a JSON object")
    return cast(dict[str, object], value)


def sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return cast(list[object], value)


def read(path: Path) -> object:
    return cast(object, json.loads(path.read_text()))


def bundle(spec: dict[str, object], shared: dict[str, object]) -> dict[str, object]:
    """Inline local schemas for validators/codegen, never fetch remote references."""
    result = deepcopy(spec)
    mapping(result["components"])["schemas"] = deepcopy(shared["$defs"])

    def rewrite(value: object) -> None:
        if isinstance(value, dict):
            node = mapping(value)
            if "$ref" in node:
                reference = str(node["$ref"])
                for prefix in ("./schemas.json#/$defs/", "#/$defs/"):
                    if reference.startswith(prefix):
                        reference = "#/components/schemas/" + reference[len(prefix) :]
                        break
                if not reference.startswith("#/"):
                    raise ValueError(f"Nonlocal reference: {reference}")
                node["$ref"] = reference
            for child in node.values():
                rewrite(child)
        elif isinstance(value, list):
            for child in sequence(value):
                rewrite(child)

    rewrite(result)
    return result


def nodes(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        item = mapping(value)
        return [item, *(node for child in item.values() for node in nodes(child))]
    if isinstance(value, list):
        return [node for child in sequence(value) for node in nodes(child)]
    return []


def resolve(document: dict[str, object], value: object) -> dict[str, object]:
    node = mapping(value)
    if "$ref" not in node:
        return node
    reference = str(node["$ref"])
    if not reference.startswith("#/"):
        raise ValueError(f"Nonlocal reference: {reference}")
    target: object = document
    for part in reference[2:].split("/"):
        target = mapping(target)[part.replace("~1", "/").replace("~0", "~")]
    return mapping(target)


def validate_contract(
    spec: dict[str, object], shared: dict[str, object], fixtures: list[object]
) -> tuple[int, int]:
    Draft202012Validator.check_schema(shared)
    document = bundle(spec, shared)
    for node in nodes(document):
        if "$ref" in node:
            resolve(document, node)
    validate(document)
    operations: dict[str, dict[str, object]] = {}
    for path in mapping(document["paths"]).values():
        for method, value in mapping(path).items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation = mapping(value)
            name = str(operation["operationId"])
            if name in operations:
                raise ValueError(f"Duplicate operation ID: {name}")
            if operation.get("security") != [{"bearerAuth": []}]:
                raise ValueError(f"Missing bearer security: {name}")
            if not operation.get("x-authorization"):
                raise ValueError(f"Missing authorization policy: {name}")
            if operation.get("x-contract-status") != "frozen":
                raise ValueError(f"Unreviewed operation in frozen spec: {name}")
            operations[name] = operation
    covered: set[tuple[str, str]] = set()
    names: set[str] = set()
    components = mapping(mapping(document["components"])["schemas"])
    for fixture in fixtures:
        example = mapping(fixture)
        name = str(example["name"])
        if name in names:
            raise ValueError(f"Duplicate example: {name}")
        names.add(name)
        oid = str(example["operation_id"])
        operation = operations[oid]
        direction = str(example["direction"])
        if direction == "request":
            container = mapping(operation["requestBody"])
        elif direction == "response":
            container = resolve(document, mapping(operation["responses"])[str(example["status"])])
            if "x-error-codes" in container:
                error = mapping(example["value"])
                if error["code"] not in sequence(container["x-error-codes"]):
                    raise ValueError(f"Error/status mismatch: {name}")
                if error["retryable"] != (str(example["status"]) in {"429", "503"}):
                    raise ValueError(f"Retryability mismatch: {name}")
        else:
            raise ValueError(f"Unknown example direction: {direction}")
        schema = mapping(mapping(mapping(container["content"])["application/json"])["schema"])
        validator_schema = {**schema, "components": {"schemas": components}}
        Draft202012Validator(validator_schema, format_checker=FormatChecker()).validate(
            example["value"]
        )
        if direction == "request" or str(example.get("status", "")).startswith("2"):
            covered.add((oid, direction))
    for name, operation in operations.items():
        if (name, "response") not in covered:
            raise ValueError(f"Missing success example: {name}")
        if "requestBody" in operation and (name, "request") not in covered:
            raise ValueError(f"Missing request example: {name}")
    return len(operations), len(fixtures)


def validate_engine_events() -> None:
    from wayfarer.simulation.events import EVENT_ADAPTER

    schema = mapping(read(ROOT / "engine-events.schema.json"))
    Draft202012Validator.check_schema(schema)
    expected = EVENT_ADAPTER.json_schema()
    actual = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
    actual["$defs"] = {
        k: v for k, v in mapping(actual["$defs"]).items() if k != "StoredEngineEvent"
    }
    if actual != expected:
        raise ValueError(
            "Engine event schema changed; regenerate and review engine-events.schema.json"
        )


def main() -> None:
    validate_engine_events()
    operations, examples = validate_contract(
        mapping(read(ROOT / "openapi.json")),
        mapping(read(ROOT / "schemas.json")),
        sequence(read(ROOT / "examples.json")),
    )
    print(f"Validated {operations} frozen operations and {examples} examples (offline)")


if __name__ == "__main__":
    main()
