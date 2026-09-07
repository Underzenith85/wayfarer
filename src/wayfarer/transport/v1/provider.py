"""Bounded provider proposals over the same authorized v1 projections."""

from __future__ import annotations

import json

from wayfarer.orchestration.providers import Narration, Orchestrator, ProviderRequest

from .common import HTTP, Obj, encoded, obj, validate
from .service import V1Service


def bind_provider(service: V1Service, orchestrator: Orchestrator) -> None:
    async def interpret(context: Obj, text: str) -> Obj:
        schema: Obj = {
            "oneOf": [
                {"$ref": "#/$defs/Intent"},
                {
                    "type": "object",
                    "properties": {"clarification": {"$ref": "#/$defs/Clarification"}},
                    "required": ["clarification"],
                    "additionalProperties": False,
                },
            ],
            "$defs": HTTP["$defs"],
        }
        raw = await orchestrator._call(
            ProviderRequest(
                operation="intent",
                session_id=service.projector.token(
                    "provider",
                    str(obj(context["campaign"])["id"]),
                    "session",
                    obj(context["character"])["id"],
                ),
                context_json=encoded(context),
                prompt=text,
                output_schema=schema,
            )
        )
        value = obj(json.loads(raw))
        if "clarification" in value:
            validate("Clarification", value["clarification"])
        else:
            validate("Intent", value)
        return value

    async def narrate(context: Obj, action: Obj) -> str:
        raw = await orchestrator._call(
            ProviderRequest(
                operation="narration",
                session_id=service.projector.token(
                    "provider", str(obj(context["campaign"])["id"]), "narration", action["actor_id"]
                ),
                context_json=encoded({"view": context, "action": action}),
                prompt="Narrate this committed result without adding mechanical consequences.",
                output_schema=Narration.model_json_schema(),
            )
        )
        return Narration.model_validate_json(raw).text

    service.interpret, service.narrate = interpret, narrate
