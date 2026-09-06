"""Installed composition root."""

import argparse
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError as SettingsValidationError

from wayfarer.config import Settings
from wayfarer.logging import configure
from wayfarer.transport.http import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Wayfarer.")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    try:
        base = Settings()
        settings = Settings(
            host=base.host,
            port=args.port if args.port is not None else base.port,
            db=args.db.expanduser().resolve() if args.db is not None else base.db,
            database_url=base.database_url,
            log_level=base.log_level,
            openai_api_key=base.openai_api_key,
            openai_model=base.openai_model,
            model_timeout_seconds=base.model_timeout_seconds,
            db_timeout_seconds=base.db_timeout_seconds,
        )
    except SettingsValidationError as exc:
        parser.error(str(exc))
    configure(settings.log_level)
    web.run_app(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        print=lambda line: print(line, flush=True),
        handle_signals=True,
    )
