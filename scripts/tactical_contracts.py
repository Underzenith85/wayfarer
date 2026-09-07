"""Generate/review additive tactical-v1 schemas without changing frozen player v1."""

import argparse
import json
from pathlib import Path

from wayfarer.orchestration.tactical_view import TacticalSnapshot
from wayfarer.transport.tactical_api import TacticalRequest


def contract() -> str:
    schemas: dict[str, object] = {}
    for model in (TacticalSnapshot, TacticalRequest):
        schema = model.model_json_schema()
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    schemas["TacticalError"] = {
        "type": "object",
        "required": ["code", "error"],
        "properties": {"code": {"type": "string"}, "error": {"type": "string"}},
    }
    path = "/campaigns/{cid}"
    parameter = {"name": "cid", "in": "path", "required": True, "schema": {"type": "string"}}

    def response(name: str) -> dict[str, object]:
        return {
            "description": name,
            "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{name}"}}},
        }

    responses = {
        "200": response("TacticalSnapshot"),
        **{str(n): response("TacticalError") for n in (400, 401, 403, 404, 409, 413, 429)},
    }
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Tactical play", "version": "1.0.0"},
        "servers": [{"url": "/api/tactical/v1"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            path: {
                "get": {
                    "operationId": "readTactical",
                    "parameters": [
                        parameter,
                        {
                            "name": "actor_id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": responses,
                }
            },
            path + "/commands": {
                "post": {
                    "operationId": "executeTactical",
                    "parameters": [parameter],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/TacticalRequest"}
                            }
                        },
                    },
                    "responses": responses,
                }
            },
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": schemas,
        },
    }
    return json.dumps(document, indent=2).replace("#/$defs/", "#/components/schemas/") + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path("contracts/tactical/v1/openapi.json")
    if args.check:
        if path.read_text() != contract():
            raise SystemExit("Tactical contract drift")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contract())
