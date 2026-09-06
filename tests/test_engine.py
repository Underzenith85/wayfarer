import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wayfarer import validation
from wayfarer.character import builder
from wayfarer.models import PublicCampaign
from wayfarer.orchestration import service as engine
from wayfarer.rules import checks
from wayfarer.simulation.scenario import scenario


class RulesTests(unittest.TestCase):
    def test_default_legal(self) -> None:
        self.assertTrue(builder.validate(builder.character())["valid"])

    def test_attack_payloads_rejected(self) -> None:
        base = builder.character()
        cases = []
        c = copy.deepcopy(base)
        c["attributes"] = {k: 14 for k in c["attributes"]}
        cases.append(c)
        c = copy.deepcopy(base)
        c["traits"] = ["Fit", "Fit"]
        cases.append(c)
        c = copy.deepcopy(base)
        c["traits"] = ["Invincible"]
        cases.append(c)
        c = copy.deepcopy(base)
        c["attributes"]["IQ"] = True
        cases.append(c)
        c = copy.deepcopy(base)
        c["skills"]["Stealth"] = -20
        cases.append(c)
        c = copy.deepcopy(base)
        invalid = validation.mapping(c)
        invalid["hp"] = 999
        self.assertFalse(builder.validate(invalid)["valid"])
        c = copy.deepcopy(base)
        c["attributes"] = {k: 8 for k in c["attributes"]}
        cases.append(c)
        c = copy.deepcopy(base)
        c["attributes"]["DX"] = 14
        c["skills"]["Stealth"] = 16
        cases.append(c)
        for c in cases:
            with self.subTest(character=c):
                self.assertFalse(builder.validate(c)["valid"])

    def test_critical_edges(self) -> None:
        with patch("wayfarer.rules.checks.secrets.randbelow", side_effect=[5, 5, 4]):
            self.assertFalse(checks.roll(16)["success"])
        with patch("wayfarer.rules.checks.secrets.randbelow", side_effect=[0, 0, 0]):
            self.assertTrue(checks.roll(2)["success"])


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old = engine.DB
        engine.DB = Path(self.tmp.name) / "test.db"
        self.ai = patch("wayfarer.orchestration.llm.enabled", return_value=False)
        self.ai.start()
        self.s = engine.create(builder.character(), scenario())

    def tearDown(self) -> None:
        engine.DB = self.old
        self.ai.stop()
        self.tmp.cleanup()

    def turn(self, text: str, key: str = "one", revision: int = 0) -> PublicCampaign:
        return engine.turn(self.s["id"], key, revision, text)

    def test_secrets_not_in_public_state(self) -> None:
        self.assertNotIn("secret", self.s["scenario"])
        self.assertNotIn("clue", self.s["scenario"])

    def test_question_no_cost(self) -> None:
        s = self.turn("Could I sneak to the customs house?")
        self.assertEqual(s["minutes"], 0)
        self.assertFalse(s["complete"])

    def test_duplicate_no_reroll(self) -> None:
        with patch(
            "wayfarer.rules.checks.roll",
            return_value={
                "success": False,
                "dice": [6, 6, 5],
                "total": 17,
                "target": 13,
                "critical": "failure",
            },
        ) as dice:
            one = self.turn("Inspect the docks")
            two = self.turn("Inspect the docks")
            self.assertEqual(one, two)
            self.assertEqual(dice.call_count, 1)
        with self.assertRaises(ValueError):
            self.turn("Rest")

    def test_stale_revision_rejected(self) -> None:
        self.turn("Rest")
        with self.assertRaises(ValueError):
            self.turn("Rest", "two", 0)

    def test_state_survives_reopen(self) -> None:
        self.turn("Rest")
        self.assertEqual(engine.read(self.s["id"])["minutes"], 30)

    def test_invalid_character_cannot_activate(self) -> None:
        c = builder.character()
        c["attributes"]["ST"] = 100
        with self.assertRaises(ValueError):
            engine.create(c, scenario())

    def test_progression_and_reward_once(self) -> None:
        with patch(
            "wayfarer.rules.checks.roll",
            return_value={
                "success": True,
                "dice": [1, 1, 1],
                "total": 3,
                "target": 13,
                "critical": "success",
            },
        ):
            self.turn("Inspect the docks")
            s = self.turn("Sneak to the customs house", "two", 1)
            self.assertTrue(s["complete"])
            self.assertIn("Courier’s letter", s["inventory"])
            s = self.turn("Sneak to the customs house", "three", 2)
            self.assertEqual(s["inventory"].count("Courier’s letter"), 1)

    def test_generation_failure_no_mutation(self) -> None:
        with (
            patch("wayfarer.orchestration.llm.enabled", return_value=True),
            patch("wayfarer.orchestration.llm.generate", side_effect=ValueError("provider failed")),
        ):
            with self.assertRaises(ValueError):
                self.turn("Inspect the docks")
        self.assertEqual(engine.read(self.s["id"])["revision"], 0)

    def test_narration_failure_keeps_commit(self) -> None:
        with (
            patch("wayfarer.orchestration.llm.enabled", return_value=True),
            patch(
                "wayfarer.orchestration.llm.generate",
                side_effect=[{"action": "rest"}, ValueError("provider failed")],
            ),
        ):
            s = self.turn("Rest")
        self.assertEqual(s["revision"], 1)
        self.assertEqual(s["minutes"], 30)


if __name__ == "__main__":
    unittest.main()
