"""Run Roadside Watch: python -m wayfarer.adventures.roadwatch_cli --tokens PATH."""

import argparse
import json
import os
from pathlib import Path

from aiohttp import web

from wayfarer.adventures.roadwatch_runtime import application
from wayfarer.runtime import campaign_store


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Roadside Watch: fixed human TL3 GURPS campaign API"
    )
    parser.add_argument(
        "--tokens",
        type=Path,
        required=True,
        help="Private JSON map of bearer tokens to party/gm identities",
    )
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="sqlite")
    parser.add_argument("--db", type=Path, default=Path("data/roadwatch.sqlite3"))
    parser.add_argument(
        "--database-url-env",
        default="WAYFARER_DATABASE_URL",
        help="Environment variable containing the PostgreSQL URL; never printed",
    )
    parser.add_argument(
        "--v1-ledger",
        type=Path,
        help="Durable action-receipt SQLite database; required for PostgreSQL",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--frontend",
        type=Path,
        help="Optional frontend build; browser scope remains separately unverified",
    )
    args = parser.parse_args()
    if args.tokens.stat().st_mode & 0o077:
        parser.error("Token file must be private to its owner (chmod 600)")
    value: object = json.loads(args.tokens.read_text())
    if (
        not isinstance(value, dict)
        or not value
        or not all(
            isinstance(key, str) and key and isinstance(principal, str) and principal
            for key, principal in value.items()
        )
    ):
        parser.error(
            "Token file must contain a nonempty map of bearer tokens to party/gm identities"
        )
    tokens = {str(key): str(principal) for key, principal in value.items()}
    database_url: str | None = None
    ledger: Path | None = args.v1_ledger
    if args.backend == "postgres":
        url = os.environ.get(args.database_url_env)
        if not url:
            parser.error("Configure the selected PostgreSQL URL environment variable")
        if ledger is None:
            parser.error("PostgreSQL requires --v1-ledger for durable action receipts")
        database_url = url
    else:
        args.db.parent.mkdir(parents=True, exist_ok=True)
    if ledger is not None:
        ledger.parent.mkdir(parents=True, exist_ok=True)
    store = campaign_store(args.db, database_url=database_url)
    web.run_app(
        application(
            store,
            tokens,
            v1_ledger_path=ledger,
            frontend=args.frontend,
            origins=frozenset({f"http://127.0.0.1:{args.port}"}),
        ),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
