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
        frontend = Path(directory) / "frontend"
        (frontend / "assets").mkdir(parents=True)
        (frontend / "index.html").write_text("<h1>WAYFARER</h1>")
        (frontend / "assets" / "smoke.js").write_text("// installed static mount")
        env["WAYFARER_FRONTEND_DIR"] = str(frontend)
        env["WAYFARER_TOKENS"] = '{"smoke-token":"smoke"}'
        env["WAYFARER_LLM_PROVIDER"] = "responses"
        env.pop("WAYFARER_OPENAI_API_KEY", None)
        env.pop("WAYFARER_OPENAI_MODEL", None)
        env.pop("WAYFARER_DATABASE_URL", None)
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
                with urllib.request.urlopen(base + "/health", timeout=0.5):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    raise RuntimeError(f"Server failed to start: {stdout} {stderr}") from None
                time.sleep(0.05)
        try:

            def get(path: str) -> bytes:
                with urllib.request.urlopen(
                    urllib.request.Request(
                        base + path, headers={"Authorization": "Bearer smoke-token"}
                    ),
                    timeout=5,
                ) as response:
                    raw: object = response.read()
                    if not isinstance(raw, bytes):
                        raise TypeError("Expected bytes")
                    return raw

            def post(path: str, data: dict[str, object]) -> dict[str, object]:
                request = urllib.request.Request(
                    base + path,
                    data=json.dumps(data).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer smoke-token",
                    },
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return validation.mapping(validation.decode(response.read()))

            assert b"WAYFARER" in get("/")
            assert get("/assets/smoke.js")
            graphs = json.loads(get("/setups/templates"))
            graph = graphs[0]
            campaign = post(
                "/setups", {"id": "wheel-smoke", "brief": graph["brief"], "graph": graph}
            )
            path = "/setups/" + validation.string(campaign["id"])
            commands: list[dict[str, object]] = [
                {"operation": "assign", "principal_id": "smoke", "actor_ids": ["mira"]},
                {"operation": "ready"},
                {"operation": "activate"},
            ]
            for revision, fields in enumerate(commands):
                command = {"id": f"smoke-{revision}", "expected_revision": revision, **fields}
                first = post(path, command)
                assert post(path, command) == first
            assert first["phase"] == "active"
            assert json.loads(get(path)) == first
            assert json.loads(get("/api/v1/campaigns"))["items"]
            print(
                "Installed wheel smoke passed: static mount, bundled scenario, create, activate, retry, reload"
            )
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    main()
