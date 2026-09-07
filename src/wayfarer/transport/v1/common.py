"""Closed wire validation and non-disclosing protocol errors."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

Obj = dict[str, object]


def obj(value: object) -> Obj:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise Fault(400, "invalid_request")
    return cast(Obj, value)


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise Fault(400, "invalid_request")
    return cast(list[object], value)


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def uid() -> str:
    return str(uuid.uuid4())


class Fault(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code

    def wire(self, request_id: str) -> Obj:
        return {
            "code": self.code,
            "message": self.code.replace("_", " ").capitalize(),
            "request_id": request_id,
            "retryable": self.status in (429, 503),
            "field_errors": [],
        }


HTTP = obj(json.loads(files(__package__).joinpath("schemas.json").read_text()))
LIVE = obj(json.loads(files(__package__).joinpath("events.schema.json").read_text()))
OPENAPI = obj(json.loads(files(__package__).joinpath("openapi.json").read_text()))
REGISTRY: Registry[bool | Mapping[str, object]] = Registry().with_resources(
    [(str(doc["$id"]), Resource.from_contents(doc)) for doc in (HTTP, LIVE)]
)


def validate(name: str, value: object, *, live: bool = False) -> Obj:
    document = LIVE if live else HTTP
    validator = Draft202012Validator(
        {"$ref": f"{document['$id']}#/$defs/{name}"},
        registry=REGISTRY,
        format_checker=FormatChecker(),
    )
    if not validator.is_valid(value):
        raise Fault(400, "invalid_request")
    return obj(value)
