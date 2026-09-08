"""Explicit v2 server social-policy contract; does not widen frozen v1 authoring."""

import argparse
import json
from pathlib import Path

from wayfarer.orchestration.fright import FrightDecision
from wayfarer.simulation.npcs import NPCSocialRules

PATH = Path(__file__).resolve().parents[1] / "contracts/social/v2/schemas.json"


def contract() -> str:
    return (
        json.dumps(
            {
                model.__name__: model.model_json_schema()
                for model in (NPCSocialRules, FrightDecision)
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        if PATH.read_text() != contract():
            raise SystemExit("Social v2 contract drift")
    else:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(contract())


if __name__ == "__main__":
    main()
