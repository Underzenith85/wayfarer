"""The new package can read the original demo database without a migration."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wayfarer.character import builder
from wayfarer.orchestration import service
from wayfarer.simulation.scenario import scenario


class MigrationTests(unittest.TestCase):
    def test_original_schema_state_and_request_ids_survive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            state = {
                "id": "legacy",
                "revision": 1,
                "rules": "wayfarer-lite-1",
                "character": builder.character(),
                "scenario": scenario(),
                "hp": 10,
                "fp": 11,
                "minutes": 30,
                "location": "Blackwater docks",
                "inventory": ["Travel clothes"],
                "discoveries": [],
                "flags": [],
                "complete": False,
                "messages": [{"role": "gm", "text": "You rest."}],
            }
            db = sqlite3.connect(path)
            with db:
                db.execute("CREATE TABLE campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)")
                db.execute(
                    "CREATE TABLE events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))"
                )
                db.execute("INSERT INTO campaigns VALUES (?, ?)", ("legacy", json.dumps(state)))
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?)",
                    ("legacy", "old-request", json.dumps({"input": "Rest"})),
                )
            db.close()
            with (
                patch.object(service, "DB", path),
                patch("wayfarer.orchestration.llm.enabled", return_value=False),
            ):
                self.assertEqual(service.read("legacy"), state)
                retried = service.turn("legacy", "old-request", 0, "Rest")
                self.assertEqual(retried["revision"], 1)
                result = service.turn("legacy", "next-request", 1, "Rest")
                self.assertEqual(result["revision"], 2)
                self.assertEqual(result["minutes"], 60)
