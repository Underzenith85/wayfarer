"""Exercise an installed wheel from a temporary cwd, without checkout imports.

Usage: python scripts/smoke_installed.py /absolute/path/to/installed/wayfarer
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from wayfarer import validation


def main() -> None:
    executable = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory() as directory:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "OPENAI_API_KEY", "OPENAI_MODEL"}
        }
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen(
            [executable, "--port", str(port), "--db", str(Path(directory) / "campaigns.sqlite3")],
            cwd=directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(base + "/api/bootstrap", timeout=0.5):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise RuntimeError(f"Server failed to start: {stdout} {stderr}") from None
                time.sleep(0.05)
        try:

            def get(path: str) -> bytes:
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    raw: object = response.read()
                    if not isinstance(raw, bytes):
                        raise TypeError("Expected bytes")
                    return raw

            def post(path: str, data: dict[str, object]) -> dict[str, object]:
                request = urllib.request.Request(
                    base + path,
                    data=json.dumps(data).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return validation.mapping(validation.decode(response.read()))

            assert b"WAYFARER" in get("/")
            assert get("/app.js") and get("/style.css")
            bootstrap = validation.mapping(validation.decode(get("/api/bootstrap")))
            campaign = post(
                "/api/campaigns",
                {"character": bootstrap["character"], "scenario": bootstrap["scenario"]},
            )
            path = "/api/campaigns/" + validation.string(campaign["id"])
            command: dict[str, object] = {
                "request_id": "wheel-smoke",
                "revision": 0,
                "text": "Rest",
            }
            first = post(path + "/turn", command)
            assert first["minutes"] == 30 and first["revision"] == 1
            assert post(path + "/turn", command) == first
            assert json.loads(get(path)) == first
            assert "secret" not in validation.mapping(first["scenario"])
            print("Installed wheel smoke passed: assets, create, turn, retry, reload, secrets")
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    main()
