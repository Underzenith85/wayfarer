"""Finite test-only provider. No network, credentials, or authoritative mutations."""

from wayfarer.adventures.lantern import adventure
from wayfarer.orchestration.providers import ProviderReply, ProviderRequest, Usage


class ReferenceProvider:
    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def complete(self, request: ProviderRequest) -> object:
        self.requests.append(request)
        if request.operation == "scenario_draft":
            payload = adventure(
                sequel='"continuation": null' not in request.context_json
                and '"continuation"' in request.context_json
            ).model_dump_json()
        elif request.operation == "narration":
            payload = '{"text":"Your choice has been recorded."}'
        elif "inspect" in request.prompt.lower():
            payload = '{"kind":"inspect","target_id":"manifest"}'
        else:
            payload = '{"kind":"wait","ticks":1}'
        return ProviderReply(payload_json=payload, usage=Usage(input_tokens=1, output_tokens=1))
