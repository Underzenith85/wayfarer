"""Generate/review additive tactical-v1 schemas without changing frozen player v1."""

import argparse
import json
from pathlib import Path

from wayfarer.orchestration.equipment_view import TacticalSnapshotV2
from wayfarer.orchestration.tactical_view import TacticalSnapshot
from wayfarer.transport.tactical_api import TacticalRequest, TacticalRequestV2


def contract(version: int = 1) -> str:
    if version not in (1, 2):
        raise ValueError("Unsupported tactical contract version")
    request_model = TacticalRequest if version == 1 else TacticalRequestV2
    schemas: dict[str, object] = {}
    snapshot_model = TacticalSnapshot if version == 1 else TacticalSnapshotV2
    for model in (snapshot_model, request_model):
        schema = model.model_json_schema()
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    # The frozen v1 request model overwrites the live TakeCombatTurn definition.
    # Remove definitions reachable only from the overwritten live model so additive
    # v2 command fields cannot drift the reviewed v1 document.
    if version == 1:
        schemas.pop("BasicMove", None)
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
        "200": response(snapshot_model.__name__),
        **{str(n): response("TacticalError") for n in (400, 401, 403, 404, 409, 413, 429)},
    }
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Tactical play", "version": f"{version}.0.0"},
        "servers": [{"url": f"/api/tactical/v{version}"}],
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
                                "schema": {"$ref": f"#/components/schemas/{request_model.__name__}"}
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
    parser.add_argument("--version", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    path = Path(f"contracts/tactical/v{args.version}/openapi.json")
    if args.check:
        if path.read_text() != contract(args.version):
            raise SystemExit("Tactical contract drift")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contract(args.version))
