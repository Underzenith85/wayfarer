"""Regenerate the existing authoring model schemas or check their drift."""

import argparse
import json
from pathlib import Path

from wayfarer.simulation.catalog import (
    CatalogCommand,
    CatalogSummary,
    InstantiateRevision,
    RevisionView,
    ScenarioGenerationJob,
    ScenarioGenerationRequest,
)
from wayfarer.simulation.scenario_document import PlayerScenarioExport, ScenarioDocument

PATH = Path(__file__).resolve().parents[1] / "contracts/authoring/v1/schemas.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    models = (
        CatalogCommand,
        InstantiateRevision,
        CatalogSummary,
        RevisionView,
        ScenarioGenerationRequest,
        ScenarioGenerationJob,
        ScenarioDocument,
        PlayerScenarioExport,
    )
    schemas = {model.__name__: model.model_json_schema() for model in models}
    if args.check:
        if json.loads(PATH.read_text()) != schemas:
            raise SystemExit("Authoring contract schema drift")
    else:
        PATH.write_text(json.dumps(schemas, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
