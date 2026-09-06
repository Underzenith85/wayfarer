from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest_asyncio

from wayfarer.config import Settings
from wayfarer.orchestration.llm import LLMClient
from wayfarer.orchestration.service import GameService


@pytest_asyncio.fixture
async def service(tmp_path: Path) -> AsyncIterator[GameService]:
    settings = Settings(db=tmp_path / "test.sqlite3")
    async with aiohttp.ClientSession() as session:
        yield GameService(settings, LLMClient(settings, session))
