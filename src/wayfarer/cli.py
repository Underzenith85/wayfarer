"""Installed composition root for the local demo."""

import argparse
import os
from http.server import ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Wayfarer local roleplaying demo.")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.getenv("WAYFARER_DB", "data/wayfarer.sqlite3")),
        help="SQLite path; relative paths are resolved from the current working directory",
    )
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    from wayfarer.orchestration import service
    from wayfarer.transport.http import Handler

    service.DB = args.db.expanduser().resolve()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"Wayfarer: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
