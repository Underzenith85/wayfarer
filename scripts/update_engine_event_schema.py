"""Regenerate engine definitions without replacing the live message contract."""

import json

from scripts.validate_contracts import ROOT, mapping, read
from wayfarer.simulation.events import EVENT_ADAPTER


def main() -> None:
    path = ROOT / "events.schema.json"
    document = mapping(read(path))
    engine = EVENT_ADAPTER.json_schema()
    definitions = mapping(engine.pop("$defs"))
    mapping(document["$defs"]).update(definitions)
    mapping(document["$defs"])["EngineEvent"] = engine
    encoded = json.dumps(document, indent=2) + "\n"
    path.write_text(encoded)
    (ROOT.parents[1] / "src/wayfarer/transport/v1/events.schema.json").write_text(encoded)


if __name__ == "__main__":
    main()
