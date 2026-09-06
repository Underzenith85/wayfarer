"""Exercise an installed wheel from a temporary cwd, without checkout imports.

Usage: python scripts/smoke_installed.py /absolute/path/to/installed/wayfarer
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
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
        process = subprocess.Popen(
            [executable, "--port", "0", "--db", str(Path(directory) / "campaigns.sqlite3")],
            cwd=directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output: queue.Queue[str] = queue.Queue()
        stdout = process.stdout
        assert stdout is not None
        reader = threading.Thread(target=lambda: output.put(stdout.readline()), daemon=True)
        reader.start()
        try:
            line = output.get(timeout=15).strip()
            assert line.startswith("Wayfarer: http://127.0.0.1:"), line
            base = line.removeprefix("Wayfarer: ")

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
