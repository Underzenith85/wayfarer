"""Emit the GURPS Basic Set certification report and fail closed when blocked."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from wayfarer.certification.basic_set_certification import evaluate

ROOT = Path(__file__).resolve().parents[1]


def repository_commit(root: Path) -> str:
    """Resolve the exact commit whose checked-out evidence is being certified."""
    revision = os.environ.get("GITHUB_SHA")
    if revision is None:
        tracked_changes = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"],
            cwd=root,
            check=False,
        ).returncode
        if tracked_changes:
            raise SystemExit("Basic Set certification cannot publish a dirty checkout")
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SystemExit("Basic Set certification requires an exact 40-character commit SHA")
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate(ROOT)
    payload = result.as_dict()
    payload["repository_commit"] = repository_commit(ROOT)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    if not result.certified:
        raise SystemExit(f"GURPS Basic Set certification blocked by {len(result.blockers)} item(s)")


if __name__ == "__main__":
    main()
