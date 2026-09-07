"""Application-owned provider lifecycle; provider selection never reaches domain rules."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import aiohttp

from wayfarer.config import Settings
from wayfarer.orchestration.codex import CodexProvider, CodexSettings, ProviderStatus
from wayfarer.orchestration.llm import LLMClient
from wayfarer.orchestration.providers import ResponsesProvider, StructuredProvider


@asynccontextmanager
async def provider_runtime(
    settings: Settings,
    *,
    status: Callable[[ProviderStatus], None] | None = None,
) -> AsyncIterator[StructuredProvider]:
    if settings.llm_provider == "codex":
        provider = CodexProvider(
            CodexSettings(
                model=settings.codex_model,
                effort=settings.codex_effort,
                timeout=min(settings.model_timeout_seconds, 120.0),
                home=settings.codex_home,
                sessions=settings.codex_sessions,
            ),
            status=status,
        )
        try:
            yield provider
        finally:
            await provider.close()
    else:
        async with aiohttp.ClientSession() as session:
            yield ResponsesProvider(LLMClient(settings, session))
