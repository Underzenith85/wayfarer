"""Untrusted data must pass runtime checks as well as static typing."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wayfarer import validation
from wayfarer.character import builder
from wayfarer.orchestration import llm, service
from wayfarer.persistence.sqlite import SQLiteStore
from wayfarer.simulation.scenario import scenario


class ValidationTests(unittest.TestCase):
    def test_character_schema_rejects_coercion_and_unknown_fields(self) -> None:
        changes: list[tuple[str, dict[str, object]]] = [
            ("boolean", {"attributes": {"ST": True, "DX": 10, "IQ": 10, "HT": 10}}),
            ("string integer", {"skills": {"Stealth": "4"}}),
            ("unknown", {"free_points": 1000}),
            ("missing", {"attributes": {"ST": 10}}),
            ("wrong collection", {"traits": "Fit"}),
        ]
        for name, change in changes:
            with self.subTest(name=name):
                payload = validation.mapping(builder.character())
                payload.update(change)
                self.assertFalse(builder.validate(payload)["valid"])

    def test_schema_returns_independent_containers(self) -> None:
        original = builder.character()
        parsed = validation.character(original)
        parsed["attributes"]["ST"] = 99
        self.assertEqual(original["attributes"]["ST"], 10)

    def test_invalid_scenarios_and_actions_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validation.scenario({"title": "Incomplete"})
        with self.assertRaises(ValueError):
            validation.action("grant_infinite_hp")
        with self.assertRaises(ValueError):
            validation.scenario({**scenario(), "secret": False})

    def test_provider_nonobject_payload_rejected(self) -> None:
        class Response:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "status": "completed",
                        "output": [{"content": [{"type": "output_text", "text": "[1,2,3]"}]}],
                    }
                ).encode()

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-only", "OPENAI_MODEL": "test-only"}),
            patch("wayfarer.orchestration.llm.urllib.request.urlopen") as request,
        ):
            request.return_value.__enter__.return_value = Response()
            with self.assertRaises(ValueError):
                llm.generate("Classify", {}, llm.ACTION_SCHEMA)

    def test_bad_model_action_cannot_mutate_campaign(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(service, "DB", Path(directory) / "test.db"),
        ):
            campaign = service.create(builder.character(), scenario())
            with (
                patch("wayfarer.orchestration.llm.enabled", return_value=True),
                patch("wayfarer.orchestration.llm.generate", return_value={"action": {"hp": 999}}),
            ):
                with self.assertRaises(ValueError):
                    service.turn(campaign["id"], "bad-action", 0, "Look")
            self.assertEqual(service.read(campaign["id"])["revision"], 0)

    def test_corrupt_saved_payload_fails_at_storage_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "test.db")
            with store.connection() as connection:
                connection.execute("INSERT INTO campaigns VALUES (?,?)", ("bad", '{"id":"bad"}'))
            with self.assertRaises(ValueError):
                store.read("bad")
