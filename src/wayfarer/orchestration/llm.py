"""Async Responses API adapter with bounded I/O and validated output."""

import asyncio
import json

import aiohttp

from wayfarer import validation
from wayfarer.config import Settings
from wayfarer.errors import ProviderError, ProviderTimeoutError

RESPONSES_URL = "https://api.openai.com/v1/responses"


class LLMClient:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession) -> None:
        self.settings = settings
        self.session = session

    @property
    def enabled(self) -> bool:
        return self.settings.llm_enabled

    async def generate(
        self, instructions: str, context: object, schema: dict[str, object]
    ) -> dict[str, object]:
        payload, _, _ = await self.generate_with_usage(instructions, context, schema)
        return payload

    async def generate_with_usage(
        self, instructions: str, context: object, schema: dict[str, object]
    ) -> tuple[dict[str, object], int | None, int | None]:
        if not self.enabled:
            raise ProviderError("Model provider is not configured")
        key = self.settings.openai_api_key
        model = self.settings.openai_model
        if key is None or model is None:
            raise ProviderError("Model provider is not configured")
        payload: dict[str, object] = {
            "model": model,
            "store": False,
            "instructions": instructions,
            "input": json.dumps(context),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "proposal",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        try:
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                async with self.session.post(
                    RESPONSES_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                ) as response:
                    if response.status >= 400:
                        await response.read()
                        raise ProviderError(f"Model provider returned HTTP {response.status}")
                    result = validation.mapping(await response.json(content_type=None))
        except TimeoutError as exc:
            raise ProviderTimeoutError("Model request timed out") from exc
        except aiohttp.ClientError as exc:
            raise ProviderError("Model request failed") from exc
        if result.get("status") != "completed":
            raise ProviderError("Model response did not complete")
        for raw_item in validation.sequence(result.get("output", [])):
            item = validation.mapping(raw_item)
            for raw_content in validation.sequence(item.get("content", [])):
                content = validation.mapping(raw_content)
                if content.get("type") == "output_text":
                    payload = validation.mapping(
                        validation.decode(validation.string(content["text"]))
                    )
                    usage = validation.mapping(result.get("usage", {}))
                    input_tokens = (
                        validation.integer(usage["input_tokens"])
                        if "input_tokens" in usage
                        else None
                    )
                    output_tokens = (
                        validation.integer(usage["output_tokens"])
                        if "output_tokens" in usage
                        else None
                    )
                    return payload, input_tokens, output_tokens
        raise ProviderError("Model returned no structured proposal")


def obj(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


STRING: dict[str, object] = {"type": "string"}
CHARACTER_SCHEMA = obj(
    {
        "name": STRING,
        "concept": STRING,
        "attributes": obj({k: {"type": "integer"} for k in ["ST", "DX", "IQ", "HT"]}),
        "skills": obj(
            {k: {"type": "integer"} for k in ["Stealth", "Observation", "Diplomacy", "Survival"]}
        ),
        "traits": {"type": "array", "items": STRING},
    }
)
SCENARIO_SCHEMA = obj(
    {k: STRING for k in ["title", "premise", "location", "contact", "clue", "secret", "objective"]}
)
ACTION_SCHEMA = obj(
    {"action": {"type": "string", "enum": ["observe", "talk", "sneak", "rest", "ask"]}}
)
NARRATION_SCHEMA = obj({"text": STRING})
