"""Installed reference-game composition using the same authenticated public API."""

from collections.abc import Mapping
from pathlib import Path

from aiohttp import web

from wayfarer.adventures.lantern import adventure, engine
from wayfarer.config import Settings
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.runtime import CampaignRuntime
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.campaign_api import create_campaign_app


def application(
    db: Path,
    tokens: Mapping[str, str],
    *,
    frontend: Path | None = None,
    settings: Settings | None = None,
    origins: frozenset[str] = frozenset({"http://127.0.0.1:8000"}),
) -> web.Application:
    return create_campaign_app(
        CampaignRuntime(PlayService(AsyncSQLiteStore(db), engine())),
        tokens,
        scenario_templates=(adventure(), adventure(sequel=True)),
        frontend_dir=frontend,
        settings=settings,
        v1_origins=origins,
        engine_controls=True,
    )
