"""Prove each configured checker rejects its intended regression.

Use the same locked tools/configuration as CI; write fixtures only in a temporary
folder, never the source tree. No intentionally broken code is committed.
"""

import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    config = str(root / "pyproject.toml")
    cases = [
        ("type", 'count: int = "wrong"\n', ["mypy", "--config-file", config], "[assignment]"),
        ("lint", "import os\n", ["ruff", "check", "--config", config], "F401"),
        (
            "format",
            "values=[1,2,3]\n",
            ["ruff", "format", "--check", "--config", config],
            "would be reformatted",
        ),
    ]
    with tempfile.TemporaryDirectory() as directory:
        for name, source, command, expected in cases:
            fixture = Path(directory) / f"broken_{name}.py"
            fixture.write_text(source)
            result = subprocess.run(
                ["uv", "run", "--frozen", *command, str(fixture)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0 or expected not in output:
                raise RuntimeError(f"{name} gate did not reject the intended defect:\n{output}")
            print(f"{name} gate rejected its injected defect")


if __name__ == "__main__":
    main()
