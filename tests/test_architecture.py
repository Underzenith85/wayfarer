"""Protect domain boundaries while the engine grows."""

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import wayfarer

REDUCER_MODULES = (
    "combat",
    "physical",
    "spells",
    "setup",
    "abilities",
    "director",
    "spell_backfires",
)


class ArchitectureTests(unittest.TestCase):
    def test_simulation_entity_names_have_one_owner(self) -> None:
        owners: dict[str, list[str]] = {}
        package = Path(wayfarer.__file__).parent / "simulation"
        for source in package.rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.ClassDef):
                    owners.setdefault(node.name, []).append(source.name)
        duplicates = {name: sorted(paths) for name, paths in owners.items() if len(paths) > 1}
        # Existing, unrelated equipment/source provenance records have distinct semantics.
        self.assertEqual(duplicates, {"Provenance": ["gurps_equipment.py", "scenario_document.py"]})

    def test_event_stream_has_only_atomic_persistence_writers(self) -> None:
        package = Path(wayfarer.__file__).parent
        writers = set()
        for source in package.rglob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    sql = node.value.upper()
                    if "INSERT INTO EVENT_STREAM" in sql:
                        writers.add(source.relative_to(package).as_posix())
                    self.assertNotIn("UPDATE EVENT_STREAM", sql, str(source))
                    self.assertNotIn("DELETE FROM EVENT_STREAM", sql, str(source))
        self.assertEqual(writers, {"persistence/async_sqlite.py", "persistence/postgres.py"})
        for writer in writers:
            tree = ast.parse((package / writer).read_text())
            commit = next(
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "commit_turn"
            )
            calls = {
                n.func.attr
                for n in ast.walk(commit)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            self.assertIn("_append_events", calls)
        boundary = (package / "orchestration/entropy.py").read_text()
        self.assertIn("CommandResolution(event, command_events(", boundary)

    def test_snapshot_loads_and_retries_use_stream_materialisation(self) -> None:
        package = Path(wayfarer.__file__).parent
        for adapter in ("async_sqlite", "postgres"):
            tree = ast.parse((package / "persistence" / f"{adapter}.py").read_text())
            methods = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
            for name in ("read", "replay", "_duplicate", "commit_turn"):
                calls = {
                    n.func.attr
                    for n in ast.walk(methods[name])
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                }
                self.assertIn("_read", calls, f"{adapter}.{name}")
            rendered = ast.unparse(methods["_read"])
            self.assertIn("snapshots.select_checkpoint", rendered)
            self.assertIn("snapshots.materialize", rendered)
            self.assertNotIn("state_after", rendered)

    def test_resolver_callbacks_do_not_read_clocks(self) -> None:
        package = Path(wayfarer.__file__).parent
        reads = {
            "time",
            "time_ns",
            "monotonic",
            "monotonic_ns",
            "perf_counter",
            "perf_counter_ns",
            "now",
            "utcnow",
            "today",
            "capture_instant",
        }
        for source in package.rglob("*.py"):
            tree = ast.parse(source.read_text())
            aliases = {
                alias.asname or alias.name: alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                for alias in node.names
            }
            functions = {
                node.name: node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call):
                    continue
                name = (
                    call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else call.func.id
                    if isinstance(call.func, ast.Name)
                    else ""
                )
                if name not in ("commit_turn", "commit_command"):
                    continue
                index = 5 if name == "commit_command" else 4
                if len(call.args) <= index:
                    continue
                target = call.args[index]
                pending: list[ast.AST] = (
                    [target]
                    if isinstance(target, ast.Lambda)
                    else (
                        [functions[target.id]]
                        if isinstance(target, ast.Name) and target.id in functions
                        else []
                    )
                )
                visited: set[str] = set()
                while pending:
                    for node in ast.walk(pending.pop()):
                        if not isinstance(node, ast.Call):
                            continue
                        called = (
                            node.func.attr
                            if isinstance(node.func, ast.Attribute)
                            else node.func.id
                            if isinstance(node.func, ast.Name)
                            else ""
                        )
                        self.assertNotIn(
                            aliases.get(called, called), reads, f"{source}:{node.lineno}"
                        )
                        if (
                            isinstance(node.func, ast.Name)
                            and called in functions
                            and called not in visited
                        ):
                            visited.add(called)
                            pending.append(functions[called])

    def test_live_commands_use_the_entropy_boundary(self) -> None:
        package = Path(wayfarer.__file__).parent
        boundary = package / "orchestration" / "entropy.py"
        for domain in ("orchestration", "transport"):
            for source in (package / domain).rglob("*.py"):
                if source == boundary:
                    continue
                for node in ast.walk(ast.parse(source.read_text())):
                    if not isinstance(node, ast.Call):
                        continue
                    if isinstance(node.func, ast.Attribute):
                        self.assertNotEqual(node.func.attr, "commit_turn", str(source))
                    if isinstance(node.func, ast.Name) and node.func.id == "commit_command":
                        self.assertIn("rng", {kw.arg for kw in node.keywords}, str(source))
                        self.assertIn("actor_id", {kw.arg for kw in node.keywords}, str(source))

    def test_orchestration_steps_stay_reviewable(self) -> None:
        """#417's service seams must not grow another giant reducer or callback."""
        package = Path(wayfarer.__file__).parent / "orchestration"
        for module in REDUCER_MODULES:
            source = package / f"{module}.py"
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert node.end_lineno is not None
                    with self.subTest(module=module, function=node.name):
                        self.assertLessEqual(node.end_lineno - node.lineno + 1, 200)

    def test_transaction_callbacks_are_straight_sequences(self) -> None:
        """Dispatch and mutable working state belong in named steps, not closures."""
        package = Path(wayfarer.__file__).parent / "orchestration"
        for module in REDUCER_MODULES:
            tree = ast.parse((package / f"{module}.py").read_text())
            for service in ast.walk(tree):
                if not isinstance(service, ast.AsyncFunctionDef):
                    continue
                callbacks = {
                    node.name: node for node in service.body if isinstance(node, ast.FunctionDef)
                }
                for call in ast.walk(service):
                    if not isinstance(call, ast.Call):
                        continue
                    seeded = isinstance(call.func, ast.Name) and call.func.id == "commit_command"
                    legacy = (
                        isinstance(call.func, ast.Attribute) and call.func.attr == "commit_turn"
                    )
                    if not (seeded or legacy):
                        continue
                    with self.subTest(module=module, function=service.name):
                        self.assertGreaterEqual(len(call.args), 6 if seeded else 5)
                        callback = call.args[5 if seeded else 4]
                        self.assertIsInstance(callback, ast.Name)
                        assert isinstance(callback, ast.Name)
                        self.assertIn(callback.id, callbacks)
                        for statement in callbacks[callback.id].body:
                            self.assertIsInstance(
                                statement, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)
                            )
                        self.assertFalse(
                            any(
                                isinstance(
                                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                                )
                                for statement in callbacks[callback.id].body
                                for node in ast.walk(statement)
                            ),
                            "A transaction callback must not define another closure",
                        )

    def test_orchestration_does_not_draw_random_values(self) -> None:
        """#415: all draws use the domain dice/selection API."""
        package = Path(wayfarer.__file__).parent / "orchestration"
        for source in package.rglob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else None
                    )
                    self.assertNotEqual(name, "randbelow", f"{source}:{node.lineno}")

    def test_rng_and_resource_helpers_do_not_require_play_service(self) -> None:
        """#415: a service handle cannot be used only as a source of rule dependencies."""
        package = Path(wayfarer.__file__).parent / "orchestration"
        for source in package.rglob("*.py"):
            for function in ast.parse(source.read_text()).body:
                if not isinstance(function, ast.FunctionDef):
                    continue
                for argument in function.args.args:
                    if not (
                        isinstance(argument.annotation, ast.Name)
                        and argument.annotation.id == "PlayService"
                    ):
                        continue
                    paths: set[str] = set()
                    for node in ast.walk(function):
                        if not isinstance(node, ast.Attribute):
                            continue
                        parts: list[str] = []
                        value: ast.expr = node
                        while isinstance(value, ast.Attribute):
                            parts.insert(0, value.attr)
                            value = value.value
                        if isinstance(value, ast.Name) and value.id == argument.arg:
                            paths.add(".".join(parts))
                    rule_only = {"rng", "engine", "engine.resources", "engine.resources.apply"}
                    self.assertFalse(
                        paths and paths <= rule_only,
                        f"{source}:{function.lineno} needs RulesContext",
                    )

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
                            if domain == "simulation":
                                self.assertNotIn(
                                    module.split(".")[0], {"secrets", "random", "time", "datetime"}
                                )
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
import importlib
import pkgutil
import wayfarer.simulation.mechanics
for module in pkgutil.iter_modules(wayfarer.simulation.mechanics.__path__):
    importlib.import_module("wayfarer.simulation.mechanics." + module.name)
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
        """Transactions end in PlayService.commit; only play touches play_json."""
        package = Path(wayfarer.__file__).parent
        allowed = {package / "orchestration" / "play.py"}
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
