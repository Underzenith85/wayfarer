"""Emit the GURPS Basic Set certification report and fail closed when blocked."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wayfarer.certification.basic_set_certification import evaluate

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate(ROOT)
    payload = result.as_dict()
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    if not result.certified:
        raise SystemExit(f"GURPS Basic Set certification blocked by {len(result.blockers)} item(s)")


if __name__ == "__main__":
    main()
