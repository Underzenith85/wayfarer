"""Installed composition root."""

import argparse
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError as SettingsValidationError

from wayfarer.config import Settings
from wayfarer.logging import configure
from wayfarer.runtime import create_runtime_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Wayfarer.")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db", type=Path)
    args = parser.parse_args()
    try:
        base = Settings()
        settings = Settings.model_validate(
            {
                **base.model_dump(),
                "port": args.port if args.port is not None else base.port,
                "db": args.db.expanduser().resolve() if args.db is not None else base.db,
            }
        )
        app = create_runtime_app(settings, settings.frontend_dir)
    except (SettingsValidationError, ValueError) as exc:
        parser.error(str(exc))
    configure(settings.log_level)
    web.run_app(
        app,
        host=settings.host,
        port=settings.port,
        print=lambda line: print(line, flush=True),
        handle_signals=True,
    )
