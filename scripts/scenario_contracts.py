"""Regenerate portable scenario contracts/examples, or verify them with --check."""

import argparse
import json
from pathlib import Path

from wayfarer.adventures.lantern import adventure, engine
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenario_documents import ScenarioDocuments, adapt_graph
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.simulation.scenario_document import (
    DraftRevision,
    PlayerScenarioExport,
    Provenance,
    PublicBrief,
    PublishedRevision,
    ScenarioDocument,
)

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "scenarios" / "v1"


def artifacts() -> dict[str, str]:
    def pretty(value: object) -> str:
        return json.dumps(value, indent=2, ensure_ascii=False) + "\n"

    configured = engine()
    # No database is opened: validation uses the pure initial-state constructor.
    studio = ScenarioStudio(
        PlayService(AsyncSQLiteStore(Path(":memory:")), configured),
        npc_reviewer=configured.reviewer,
    )
    service = ScenarioDocuments(studio)
    graph = adventure()
    public = PublicBrief(
        title="The Last Lantern",
        summary="Investigate missing relief supplies before the last ferry leaves.",
        tags=("mystery", "rescue"),
        setup=graph.brief,
        opening_prompt="Start at Lantern Harbor. Inspect the manifest or speak with the ferrymen.",
    )
    base = adapt_graph(
        graph,
        studio=studio,
        public=public,
        revision_id="last-lantern-published-1",
        author="Wayfarer",
    )
    authored = base.model_copy(
        update={
            "provenance": Provenance(kind="authored", author="Wayfarer"),
            "gm_notes": "Keep the warden's plans and unrevealed facts private.",
        }
    )
    generated = base.model_copy(
        update={
            "revision_id": "last-lantern-proposal-2",
            "revision": 2,
            "provenance": Provenance(
                kind="generated",
                author="Wayfarer example author",
                generator="synthetic-example (no live model call)",
                source_digest=authored.digest,
            ),
        }
    )
    result = {}
    for name, model in (
        ("document", ScenarioDocument),
        ("player", PlayerScenarioExport),
        ("draft", DraftRevision),
        ("published", PublishedRevision),
    ):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        result[f"{name}.schema.json"] = pretty(schema)
    for name, document in (("authored", authored), ("generated", generated)):
        report = service.validate(document.canonical())
        if report.status != "playable":
            raise ValueError(report)
        result[f"{name}.example.json"] = pretty(document.model_dump(mode="json"))
    revision = service.publish(
        service.save_draft(authored.canonical(), draft_id="example", principal_id="gm"),
        principal_id="gm",
    )
    result["player.example.json"] = pretty(service.player_export(revision).model_dump(mode="json"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for name, content in artifacts().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Scenario contract drift: {path}")
        else:
            ROOT.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
