"""Run: python -m wayfarer.adventures --tokens /private/tokens.json."""

import argparse
import json
from pathlib import Path

from aiohttp import web

from wayfarer.adventures.runtime import application
from wayfarer.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Play The Last Lantern using the authenticated engine"
    )
    parser.add_argument(
        "--tokens",
        type=Path,
        required=True,
        help="Private JSON map of bearer tokens to player IDs; include a gm identity",
    )
    parser.add_argument("--db", type=Path, default=Path("data/lantern.sqlite3"))
    parser.add_argument("--frontend", type=Path, help="Built frontend/dist directory")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    value: object = json.loads(args.tokens.read_text())
    if (
        not isinstance(value, dict)
        or not value
        or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items())
    ):
        parser.error("Token file must contain a nonempty map of token strings to player IDs")
    tokens = {str(k): str(v) for k, v in value.items()}
    settings = Settings()
    web.run_app(
        application(
            args.db,
            tokens,
            frontend=args.frontend,
            origins=frozenset({f"http://127.0.0.1:{args.port}"}),
            settings=settings if settings.llm_enabled or settings.llm_provider == "codex" else None,
        ),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
