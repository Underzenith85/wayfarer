"""Regenerate engine definitions without replacing the live message contract."""

import json

from scripts.validate_contracts import ROOT, mapping, read
from wayfarer.simulation.events import EVENT_ADAPTER


def main() -> None:
    path = ROOT / "engine-events.schema.json"
    previous = mapping(read(path))
    document = EVENT_ADAPTER.json_schema()
    document["$schema"] = previous["$schema"]
    document["$id"] = previous["$id"]
    document["$defs"]["StoredEngineEvent"] = mapping(previous["$defs"])["StoredEngineEvent"]
    encoded = json.dumps(document, indent=2) + "\n"
    path.write_text(encoded)
    (ROOT.parents[1] / "src/wayfarer/transport/v1/engine-events.schema.json").write_text(encoded)


if __name__ == "__main__":
    main()
