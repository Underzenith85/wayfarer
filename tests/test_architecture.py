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
    def test_encounters_reference_templates_without_embedding_maps(self) -> None:
        from wayfarer.engine.simulation.combat import Encounter

        self.assertNotIn("hex_battlefield", Encounter.model_fields)
        self.assertNotIn("HexBattlefield", Encounter.model_json_schema().get("$defs", {}))
        self.assertNotIn("battlefield_id", Encounter.model_fields)
        self.assertNotIn("spatial_kind", Encounter.model_fields)
        self.assertIn("spatial_context", Encounter.model_fields)

    def test_simulation_entity_names_have_one_owner(self) -> None:
        owners: dict[str, list[str]] = {}
        package = Path(wayfarer.__file__).parent
        for source in (package / "engine" / "simulation").rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.ClassDef):
                    owners.setdefault(node.name, []).append(source.relative_to(package).as_posix())
        duplicates = {name: sorted(paths) for name, paths in owners.items() if len(paths) > 1}
        # Existing, unrelated equipment/source provenance records have distinct semantics.
        self.assertEqual(
            duplicates,
            {
                "Provenance": [
                    "engine/simulation/gurps_equipment.py",
                    "engine/simulation/scenario_document.py",
                ]
            },
        )

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
        """The engine depends inward only: on its own domains and the shared kernel."""
        package = Path(wayfarer.__file__).parent
        allowed = {
            "rules": {"rules"},
            "character": {"rules", "character"},
            "simulation": {"rules", "character", "simulation", "world"},
        }
        kernel = {"models", "validation", "errors"}
        forbidden = {"sqlite3", "http", "urllib", "socket", "requests", "httpx", "openai", "os"}
        for domain, dependencies in allowed.items():
            for source in (package / "engine" / domain).rglob("*.py"):
                tree = ast.parse(source.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0, f"Use absolute imports: {source}")
                        modules = [node.module or ""]
                        if node.module == "wayfarer":
                            modules += [f"wayfarer.{alias.name}" for alias in node.names]
                        if node.module == "wayfarer.engine":
                            modules += [f"wayfarer.engine.{alias.name}" for alias in node.names]
                    else:
                        continue
                    for module in modules:
                        with self.subTest(source=source, module=module):
                            self.assertNotIn(module.split(".")[0], forbidden)
                            if domain == "simulation":
                                self.assertNotIn(
                                    module.split(".")[0], {"secrets", "random", "time", "datetime"}
                                )
                            if not module.startswith("wayfarer."):
                                continue
                            parts = module.split(".")
                            if parts[1] == "engine":
                                self.assertIn(parts[2], dependencies)
                            else:
                                self.assertIn(parts[1], kernel)

    def test_engine_packages_are_not_facades(self) -> None:
        """A package init declares the package's own rules or nothing; it never re-exports.

        Importing a domain must not drag in its siblings, or a noun would load a verb.
        The three inventory packages are themselves the module, so they may import what
        they are built from; an init that only imports is a facade and is rejected.
        """
        package = Path(wayfarer.__file__).parent / "engine"
        for source in package.rglob("__init__.py"):
            tree = ast.parse(source.read_text())
            imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            declarations = [
                n
                for n in tree.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.Assign, ast.AnnAssign))
            ]
            with self.subTest(source=source):
                self.assertFalse(
                    imports and not declarations, f"{source} re-exports instead of declaring"
                )

    def test_simulation_packages_declare_nothing(self) -> None:
        """Simulation package inits stay empty so no import order can load a verb early."""
        package = Path(wayfarer.__file__).parent / "engine" / "simulation"
        for source in package.rglob("__init__.py"):
            tree = ast.parse(source.read_text())
            with self.subTest(source=source):
                self.assertEqual([type(node) for node in tree.body], [ast.Expr])

    def test_engine_nouns_do_not_import_verbs(self) -> None:
        """A verb resolves mechanics with RulesContext; the nouns it reads never import one.

        Only imports the module always pays for count: a deferred import inside a
        function body is how the remaining mechanic cycles are broken today.
        """
        package = Path(wayfarer.__file__).parent
        simulation = package / "engine" / "simulation"
        trees = {source: ast.parse(source.read_text()) for source in simulation.rglob("*.py")}

        def imported(tree: ast.Module) -> set[str]:
            deferred = {
                node
                for parent in ast.walk(tree)
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                for node in ast.walk(parent)
            }
            return {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node not in deferred
            }

        def name(source: Path) -> str:
            relative = source.relative_to(package).with_suffix("")
            return "wayfarer." + ".".join(relative.parts)

        context = "wayfarer.engine.simulation.rules_context"
        verbs = {name(source) for source, tree in trees.items() if context in imported(tree)}
        for source, tree in trees.items():
            if name(source) in verbs:
                continue
            for module in sorted(imported(tree) & verbs):
                with self.subTest(source=source, module=module):
                    self.fail(f"{source} imports the verb {module}")

    def test_domain_import_does_not_load_adapters(self) -> None:
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import sys
import importlib
import pkgutil
import wayfarer.engine
for module in pkgutil.walk_packages(wayfarer.engine.__path__, "wayfarer.engine."):
    importlib.import_module(module.name)
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
import wayfarer.engine.simulation.actions
assert 'wayfarer.engine.simulation.action_engine' not in sys.modules
assert 'wayfarer.engine.simulation.rules_context' not in sys.modules
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
            "engine",
            "engine.rules",
            "engine.character",
            "engine.simulation",
            "certification",
            "persistence",
            "orchestration",
            "transport",
        ):
            importlib.import_module(f"wayfarer.{name}")


def test_prototype_resolver_and_transcript_writers_are_retired() -> None:
    from wayfarer import models
    from wayfarer.orchestration.service import GameService

    assert not (Path(wayfarer.__file__).parent / "engine/simulation/resolution.py").exists()
    assert not hasattr(GameService, "turn") and not hasattr(GameService, "interpret")
    assert not hasattr(models, "Action") and not hasattr(models, "Event")
    assert set(models.CommandReceipt.__annotations__) == {"action", "outcome"}
