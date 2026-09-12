"""Protect domain boundaries while the engine grows."""

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

import wayfarer

# The kernel every layer may import. ``contracts`` is deliberately absent: the
# application payloads it holds sit above the engine (#566).
KERNEL = frozenset({"models", "validation", "errors"})
KERNEL_VOCABULARY = frozenset(
    {
        "Record",
        "Id",
        "Count",
        "Tick",
        "Character",
        "ValidationResult",
        "Roll",
        "RulesPackagePin",
        "RulesReference",
    }
)
APPLICATION_PAYLOADS = frozenset(
    {
        "Message",
        "Campaign",
        "EventAction",
        "CommandReceipt",
        "PublicCampaign",
        "CommittedTurn",
        "ReplayedTurn",
        "TurnResult",
    }
)

REDUCER_MODULES = (
    "combat",
    "physical",
    "spells",
    "setup",
    "abilities",
    "director",
    "spell_backfires",
)


# Engine functions that still branch more than BRANCH_LIMIT times, with the count
# each is allowed today.  An entry may shrink or disappear; it may never grow, and
# no new name may be added.  See "Branching" in docs/architecture.md.
BRANCH_LIMIT = 15
BRANCHING: dict[tuple[str, str], int] = {
    ("engine/character/compiler.py", "__init__"): 17,
    ("engine/character/compiler.py", "compile"): 59,
    ("engine/character/skills.py", "__init__"): 29,
    ("engine/character/skills.py", "_conditions_satisfied"): 16,
    ("engine/character/skills.py", "compile"): 36,
    ("engine/rules/skills/mundane/__init__.py", "validate_inventory"): 35,
    ("engine/simulation/abilities.py", "apply_ability"): 51,
    ("engine/simulation/action_engine/engine.py", "_resolve_action"): 18,
    ("engine/simulation/action_engine/engine.py", "assess"): 40,
    ("engine/simulation/campaign/lifecycle.py", "validate_lifecycle"): 40,
    ("engine/simulation/combat/criticals/limbs.py", "resolve_limb"): 17,
    ("engine/simulation/combat/engine.py", "validate"): 28,
    ("engine/simulation/combat/firearm_transitions.py", "service"): 22,
    ("engine/simulation/combat/melee/resolution.py", "resolve_melee"): 62,
    ("engine/simulation/combat/ranged/attack.py", "prepare"): 19,
    ("engine/simulation/combat/ranged/resolution.py", "resolve"): 71,
    ("engine/simulation/combat/ranged/situation.py", "validate_command"): 34,
    ("engine/simulation/combat/ranged/readiness.py", "reload"): 22,
    ("engine/simulation/combat/tactical_transitions.py", "prepare_defense"): 17,
    ("engine/simulation/combat/thrown/explosions.py", "resolve_blast"): 30,
    ("engine/simulation/combat/turns.py", "apply_turn"): 46,
    ("engine/simulation/combat/unarmed/declaration.py", "validate_action"): 44,
    ("engine/simulation/combat/unarmed/fighters.py", "guard_control"): 18,
    ("engine/simulation/combat/unarmed/resolution.py", "defend"): 29,
    ("engine/simulation/equipment/catalog.py", "valid_entries"): 33,
    ("engine/simulation/equipment/catalog.py", "valid_range"): 32,
    ("engine/simulation/equipment/objects.py", "apply_object"): 20,
    ("engine/simulation/equipment/repair_transitions.py", "repair"): 22,
    ("engine/simulation/health/fatigue.py", "apply_fatigue"): 17,
    ("engine/simulation/health/hazards.py", "apply_hazard"): 35,
    ("engine/simulation/health/hit_locations.py", "wound_factor"): 16,
    ("engine/simulation/health/injury.py", "apply_injury"): 74,
    ("engine/simulation/health/medical/advanced.py", "_apply_advanced_recovery"): 34,
    ("engine/simulation/health/medical/recovery.py", "apply_recovery"): 56,
    ("engine/simulation/magic/backfire_transitions.py", "_select_backfire"): 21,
    ("engine/simulation/magic/missiles.py", "resolve"): 18,
    ("engine/simulation/magic/spell_transitions.py", "approved_context"): 22,
    ("engine/simulation/magic/spells.py", "apply_spell"): 67,
    ("engine/simulation/movement/transport.py", "apply_transport"): 33,
    ("engine/simulation/movement/vehicles/collisions.py", "impact"): 26,
    ("engine/simulation/movement/vehicles/motion.py", "control_vehicle"): 22,
    ("engine/simulation/movement/vehicles/motion.py", "move_vehicle"): 30,
    ("engine/simulation/movement/vehicles/operations/collisions.py", "resolve"): 38,
    ("engine/simulation/movement/vehicles/operations/water.py", "resolve"): 17,
    ("engine/simulation/resource_engine.py", "apply"): 45,
    ("engine/simulation/resource_engine.py", "validate"): 36,
    ("engine/simulation/social/social.py", "apply_social"): 29,
}


def reducer_sources(module: str) -> tuple[Path, ...]:
    """The sources of one reducer seam, whether it is a module or a package."""
    package = Path(wayfarer.__file__).parent / "orchestration"
    single = package / f"{module}.py"
    if single.exists():
        return (single,)
    return tuple(sorted((package / module).rglob("*.py")))


class ArchitectureTests(unittest.TestCase):
    def test_encounters_reference_templates_without_embedding_maps(self) -> None:
        from wayfarer.engine.simulation.combat.encounter import Encounter

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
                    "engine/simulation/campaign/scenario_document.py",
                    "engine/simulation/equipment/catalog.py",
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

    def test_engine_branching_only_shrinks(self) -> None:
        """A rule is read one branch at a time; the long ladders may only get shorter."""
        package = Path(wayfarer.__file__).parent
        counted: dict[tuple[str, str], int] = {}
        for source in sorted((package / "engine").rglob("*.py")):
            name = source.relative_to(package).as_posix()
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    branches = sum(isinstance(n, ast.If) for n in ast.walk(node))
                    if branches > BRANCH_LIMIT:
                        counted[(name, node.name)] = max(
                            counted.get((name, node.name), 0), branches
                        )
        for key, branches in sorted(counted.items()):
            allowed = BRANCHING.get(key)
            with self.subTest(function=key):
                self.assertIsNotNone(
                    allowed,
                    f"{key[0]}:{key[1]} has {branches} branches; dispatch on the kind instead",
                )
                assert allowed is not None
                self.assertLessEqual(branches, allowed, f"{key[0]}:{key[1]} grew a branch")
        for key, allowed in sorted(BRANCHING.items()):
            with self.subTest(function=key):
                self.assertLessEqual(
                    counted.get(key, 0), allowed, f"{key[0]}:{key[1]} is stale in BRANCHING"
                )

    def test_orchestration_steps_stay_reviewable(self) -> None:
        """#417's service seams must not grow another giant reducer or callback."""
        for module in REDUCER_MODULES:
            for source in reducer_sources(module):
                tree = ast.parse(source.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert node.end_lineno is not None
                        with self.subTest(module=source.name, function=node.name):
                            self.assertLessEqual(node.end_lineno - node.lineno + 1, 200)

    def test_transaction_callbacks_are_straight_sequences(self) -> None:
        """Dispatch and mutable working state belong in named steps, not closures."""
        for module in REDUCER_MODULES:
            for source in reducer_sources(module):
                self.check_straight_callbacks(source, module)

    def check_straight_callbacks(self, source: Path, module: str) -> None:
        tree = ast.parse(source.read_text())
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
                legacy = isinstance(call.func, ast.Attribute) and call.func.attr == "commit_turn"
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
                            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
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
        """The engine depends inward only: on its own domains and the shared kernel.

        The kernel is exactly ``models``, ``validation`` and ``errors``. The campaign
        envelope, receipts and turn results in ``contracts`` are application payloads,
        so an engine module that reaches them fails here.
        """
        package = Path(wayfarer.__file__).parent
        allowed = {
            "rules": {"rules"},
            "character": {"rules", "character"},
            "simulation": {"rules", "character", "simulation", "world"},
        }
        kernel = KERNEL
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
                            if not module.startswith("wayfarer."):
                                continue
                            parts = module.split(".")
                            if parts[1] == "engine":
                                self.assertIn(parts[2], dependencies)
                            else:
                                self.assertIn(parts[1], kernel, f"{source} reaches past the kernel")

    def test_kernel_and_contracts_keep_their_line(self) -> None:
        """The kernel imports only itself; contracts embed it; the engine never reads them.

        ``models`` declares the entity contract and the vocabulary the engine emits,
        ``contracts`` declares the payloads the application carries, and neither the
        kernel nor any engine module imports ``contracts``.
        """
        package = Path(wayfarer.__file__).parent

        def declared(source: Path) -> set[str]:
            names: set[str] = set()
            for node in ast.parse(source.read_text()).body:
                if isinstance(node, ast.ClassDef):
                    names.add(node.name)
                elif isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
                    names.add(node.name.id)
                elif isinstance(node, ast.Assign):
                    names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            return names

        def imported(source: Path) -> set[str]:
            modules: set[str] = set()
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    modules |= {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    modules.add(node.module or "")
                    if node.module == "wayfarer":
                        modules |= {f"wayfarer.{alias.name}" for alias in node.names}
            return {m for m in modules if m.startswith("wayfarer")}

        self.assertEqual(declared(package / "models.py"), set(KERNEL_VOCABULARY))
        self.assertTrue(APPLICATION_PAYLOADS <= declared(package / "contracts.py"))
        for name in KERNEL:
            for module in imported(package / f"{name}.py"):
                with self.subTest(kernel=name, module=module):
                    self.assertIn(module.split(".")[1], KERNEL)
        for source in (package / "engine").rglob("*.py"):
            with self.subTest(source=source):
                self.assertNotIn("wayfarer.contracts", imported(source))

    def test_engine_does_not_import_entropy(self) -> None:
        """Randomness and clocks enter below orchestration only through explicit values."""
        package = Path(wayfarer.__file__).parent / "engine"
        forbidden = {"secrets", "random", "time", "datetime"}
        for source in package.rglob("*.py"):
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    with self.subTest(source=source, module=module):
                        self.assertNotIn(module.split(".")[0], forbidden)

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

    def test_deferred_imports_are_explained(self) -> None:
        """A function-local wayfarer import says which cycle or gate it respects.

        Promoting one to module level is the default; the few that cannot be
        promoted are the interesting ones, so each carries a ``# deferred:`` note
        on the line above saying why.  The notes are the allowlist -- there is no
        list here to fall out of date.
        """
        package = Path(wayfarer.__file__).parent
        for source in sorted(package.rglob("*.py")):
            text = source.read_text()
            tree = ast.parse(text)
            lines = text.splitlines()
            guarded = {
                node
                for parent in ast.walk(tree)
                if isinstance(parent, ast.If) and "TYPE_CHECKING" in ast.unparse(parent.test)
                for node in ast.walk(parent)
            }
            for parent in ast.walk(tree):
                if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(parent):
                    if node in guarded:
                        continue
                    if isinstance(node, ast.ImportFrom):
                        local = bool(node.level) or (node.module or "").startswith("wayfarer")
                    elif isinstance(node, ast.Import):
                        local = any(a.name.startswith("wayfarer") for a in node.names)
                    else:
                        continue
                    if not local:
                        continue
                    # The note may run over several lines; read the whole block.
                    block: list[str] = []
                    for previous in reversed(lines[: node.lineno - 1]):
                        stripped = previous.strip()
                        if not stripped.startswith("#"):
                            break
                        block.append(stripped)
                    with self.subTest(source=source, line=node.lineno):
                        self.assertTrue(
                            any(line.startswith("# deferred:") for line in block),
                            f"{source}:{node.lineno} defers a wayfarer import without a "
                            '"# deferred:" note saying which cycle or gate it respects',
                        )

    def test_modules_import_in_one_order(self) -> None:
        """No module-level import cycle: a package must be importable from any entry.

        Type checking resolves a cycle happily; the interpreter does not, so this
        looks at what every module pays for on import, excluding TYPE_CHECKING
        blocks and deferred imports inside function bodies.
        """
        package = Path(wayfarer.__file__).parent

        def name(source: Path) -> str:
            parts = source.relative_to(package.parent).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts)

        modules = {name(source): source for source in package.rglob("*.py")}
        graph: dict[str, set[str]] = {}
        for module, source in modules.items():
            tree = ast.parse(source.read_text())
            deferred = {
                node
                for parent in ast.walk(tree)
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                or (isinstance(parent, ast.If) and "TYPE_CHECKING" in ast.unparse(parent.test))
                for node in ast.walk(parent)
            }
            edges: set[str] = set()
            for node in ast.walk(tree):
                if node in deferred:
                    continue
                if isinstance(node, ast.ImportFrom):
                    if node.level:
                        # A relative import is an edge too: resolve it against this
                        # module's package before deciding whether it leaves wayfarer.
                        anchor = (
                            module if source.name == "__init__.py" else module.rpartition(".")[0]
                        )
                        for _ in range(node.level - 1):
                            anchor = anchor.rpartition(".")[0]
                        target = f"{anchor}.{node.module}" if node.module else anchor
                    else:
                        target = node.module or ""
                    if not target.startswith("wayfarer"):
                        continue
                    edges.add(target)
                    # ``from package import submodule`` depends on the submodule, not
                    # just on the package init that does not mention it.
                    edges |= {f"{target}.{a.name}" for a in node.names} & modules.keys()
                elif isinstance(node, ast.Import):
                    edges |= {a.name for a in node.names if a.name.startswith("wayfarer")}
            graph[module] = edges

        colour: dict[str, int] = {}
        path: list[str] = []

        def walk(module: str) -> None:
            colour[module] = 1
            path.append(module)
            for imported in sorted(graph.get(module, set())):
                if imported not in graph:
                    continue
                if colour.get(imported) == 1:
                    cycle = path[path.index(imported) :] + [imported]
                    self.fail("Import cycle: " + " -> ".join(cycle))
                if colour.get(imported, 0) == 0:
                    walk(imported)
            path.pop()
            colour[module] = 2

        sys.setrecursionlimit(10000)
        for module in sorted(graph):
            if colour.get(module, 0) == 0:
                walk(module)

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
        for source in (
            *package.rglob("orchestration/**/*.py"),
            *package.rglob("transport/**/*.py"),
        ):
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
    from wayfarer import contracts, models
    from wayfarer.orchestration.service import GameService

    assert not (Path(wayfarer.__file__).parent / "engine/simulation/resolution.py").exists()
    assert not hasattr(GameService, "turn") and not hasattr(GameService, "interpret")
    for module in (models, contracts):
        assert not hasattr(module, "Action") and not hasattr(module, "Event")
    assert set(contracts.CommandReceipt.__annotations__) == {"action", "outcome"}
