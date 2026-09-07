"""Generate the additive workshop contract independently of frozen gameplay v1."""

import argparse
import json
from pathlib import Path

from wayfarer.orchestration.advancement import AdvanceCharacter, GrantPoints
from wayfarer.orchestration.workshop import DraftCommand
from wayfarer.orchestration.workshop_options import (
    ProfilePreviewRequest,
    ProfilePreviewResult,
    WorkshopOptions,
    WorkshopReviewQueue,
)


def contract() -> str:
    schemas: dict[str, object] = {}
    for model in (
        WorkshopOptions,
        ProfilePreviewRequest,
        ProfilePreviewResult,
        AdvanceCharacter,
        DraftCommand,
        WorkshopReviewQueue,
        GrantPoints,
    ):
        schema = model.model_json_schema()
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Character workshop", "version": "1.0.0"},
        "paths": {},
        "components": {"schemas": schemas},
    }
    return json.dumps(document, indent=2).replace("#/$defs/", "#/components/schemas/") + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path("contracts/workshop/v1/openapi.json")
    if args.check:
        if path.read_text() != contract():
            raise SystemExit("Workshop contract drift")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contract())
