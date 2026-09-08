"""Reviewable additive scene-ownership commands; frozen player v1 is unchanged."""

import argparse
import json
from pathlib import Path

from wayfarer.orchestration.advancement import ApplyMigration
from wayfarer.orchestration.combat import StartEncounter
from wayfarer.orchestration.encounter_scenes import MigrateEncounterScenes

PATH = Path("contracts/encounter-scenes/v1/commands.json")


def contract() -> str:
    return (
        json.dumps(
            {
                model.__name__: model.model_json_schema()
                for model in (StartEncounter, MigrateEncounterScenes, ApplyMigration)
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        if PATH.read_text() != contract():
            raise SystemExit("Encounter scene contracts drifted; regenerate and review")
    else:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        PATH.write_text(contract())
