"""Protect domain boundaries while the engine grows."""

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import wayfarer


class ArchitectureTests(unittest.TestCase):
    def test_domain_imports_are_independent(self) -> None:
        package = Path(wayfarer.__file__).parent
        allowed = {
            "rules": {"rules", "models", "validation", "errors"},
            "character": {"rules", "character", "models", "validation", "errors"},
            "simulation": {
                "rules",
                "character",
                "simulation",
                "models",
                "validation",
                "errors",
                "world",
            },
        }
        forbidden = {"sqlite3", "http", "urllib", "socket", "requests", "httpx", "openai", "os"}
        for domain, dependencies in allowed.items():
            for source in (package / domain).rglob("*.py"):
                tree = ast.parse(source.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0, f"Use absolute imports: {source}")
                        modules = [node.module or ""]
                        if node.module == "wayfarer":
                            modules += [f"wayfarer.{alias.name}" for alias in node.names]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(source=source, module=module):
                            self.assertNotIn(module.split(".")[0], forbidden)
                            if module.startswith("wayfarer."):
                                self.assertIn(module.split(".")[1], dependencies)

    def test_domain_import_does_not_load_adapters(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
import wayfarer.simulation.resolution
assert not any(m.startswith(('wayfarer.persistence', 'wayfarer.orchestration', 'wayfarer.transport')) for m in sys.modules)
assert 'sqlite3' not in sys.modules
""",
            ],
            check=True,
        )

    def test_entity_contract_is_declared_once(self) -> None:
        """Every entity subclasses wayfarer.models.Record rather than restating its config."""
        package = Path(wayfarer.__file__).parent
        for source in package.rglob("*.py"):
            if source == package / "models.py":
                continue
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
                    with self.subTest(source=source, cls=node.name):
                        self.assertNotIn("BaseModel", bases)

    def test_action_entities_do_not_load_the_engine(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
import wayfarer.simulation.actions
assert 'wayfarer.simulation.action_engine' not in sys.modules
""",
            ],
            check=True,
        )

    def test_play_checkpoints_are_written_by_one_verb(self) -> None:
        """Transactions end in PlayService.commit; only play and setup touch play_json."""
        package = Path(wayfarer.__file__).parent
        allowed = {package / "orchestration" / "play.py", package / "orchestration" / "setup.py"}
        for source in (*package.rglob("orchestration/*.py"), *package.rglob("transport/**/*.py")):
            if source in allowed:
                continue
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    elements = target.elts if isinstance(target, ast.Tuple) else [target]
                    for element in elements:
                        if (
                            isinstance(element, ast.Subscript)
                            and isinstance(element.slice, ast.Constant)
                            and element.slice.value == "play_json"
                        ):
                            self.fail(f"{source}:{node.lineno} writes play_json directly")

    def test_all_packages_import(self) -> None:
        for name in (
            "rules",
            "character",
            "simulation",
            "persistence",
            "orchestration",
            "transport",
        ):
            importlib.import_module(f"wayfarer.{name}")
