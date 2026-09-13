"""Protect domain boundaries while the engine grows."""

import ast
import importlib
import subprocess
import sys
import unittest
from pathlib import Path
from typing import get_args

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
        "CommittedTurn",
        "ReplayedTurn",
        "TurnResult",
    }
)

# The same rule for the layers above the engine (#631). Behaviour that differs by
# kind belongs in a registered object, not in another ladder: an entry may shrink
# or disappear, it may never grow, and no new name may be added.
ORCHESTRATION_BRANCHING: dict[tuple[str, str], int] = {
    ("orchestration/abilities.py", "_prepare_ability"): 18,
    ("orchestration/codex.py", "strict_schema"): 16,
    ("orchestration/combat/encounters.py", "_prepare_encounter"): 16,
    ("orchestration/combat/preflight.py", "_prepare_command"): 20,
    ("orchestration/combat/roster.py", "_join"): 20,
    ("orchestration/combat/turns.py", "_validate_turn"): 22,
    ("orchestration/fright_builds.py", "validate_change"): 17,
    ("orchestration/hazard_care.py", "execute"): 18,
    ("orchestration/hazard_care.py", "resolve"): 17,
    ("orchestration/hazards.py", "execute"): 20,
    ("orchestration/hazards.py", "resolve"): 18,
    ("orchestration/noncombat.py", "reduce"): 22,
    ("orchestration/npcs.py", "social_occurrence"): 20,
    ("orchestration/party.py", "reduce"): 21,
    ("orchestration/play.py", "initial_state"): 17,
    ("orchestration/recovery.py", "_effect"): 20,
    ("orchestration/runtime.py", "_execute"): 18,
    ("orchestration/tactical_view/hex_choices.py", "choices"): 28,
    ("orchestration/tactical_view/preview.py", "preview"): 18,
    ("orchestration/transformations.py", "execute"): 28,
    ("orchestration/transformations.py", "reduce"): 21,
    ("orchestration/workshop.py", "execute"): 18,
    ("orchestration/workshop.py", "resolve"): 16,
    ("transport/tactical_api.py", "execute"): 18,
    ("transport/v1/http.py", "route"): 28,
    ("transport/v1/live.py", "live"): 62,
    ("transport/v1/service.py", "resolve"): 20,
}

# The store handles belong to the runtime. ``persistence`` builds them, the three
# composition roots wire them, and nothing else names a store constructor.
STORE_CONSTRUCTORS = frozenset(
    {"AsyncSQLiteStore", "AsyncPostgresStore", "CatalogStore", "JobStore"}
)
STORE_OWNERS = frozenset({"runtime.py", "adventures/runtime.py", "orchestration/runtime.py"})
# Nothing outside persistence and the composition roots opens a store (#634 emptied
# this allowlist by deleting the pre-runtime creation paths). It stays empty.
STORE_CONSTRUCTOR_ALLOWLIST: frozenset[str] = frozenset()


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

    def test_orchestration_holds_no_module_level_runtime_state(self) -> None:
        """#631: a registry or a worker belongs to a runtime, never to the process."""
        package = Path(wayfarer.__file__).parent
        owned = {"SessionRegistry", "ProviderJobs", "WeakKeyDictionary"}
        for source in sorted((package / "orchestration").rglob("*.py")):
            tree = ast.parse(source.read_text())
            # A constructor inside a function body runs per instance; one outside
            # any function body runs once per process and is the thing banned here.
            inside = {
                node
                for parent in ast.walk(tree)
                if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef))
                for node in ast.walk(parent)
            }
            for call in ast.walk(tree):
                if not isinstance(call, ast.Call) or call in inside:
                    continue
                name = (
                    call.func.id
                    if isinstance(call.func, ast.Name)
                    else call.func.attr
                    if isinstance(call.func, ast.Attribute)
                    else ""
                )
                self.assertNotIn(
                    name,
                    owned,
                    f"{source}:{call.lineno} builds a {name} at module scope; "
                    "inject it from the runtime instead",
                )
            self.assertNotIn("jobs_for", ast.unparse(tree), str(source))

    def test_stores_are_constructed_by_the_runtime(self) -> None:
        """#631: only persistence and the composition roots name a store constructor."""
        package = Path(wayfarer.__file__).parent
        for source in sorted(package.rglob("*.py")):
            relative = source.relative_to(package).as_posix()
            if relative.startswith("persistence/") or relative in STORE_OWNERS:
                continue
            if relative in STORE_CONSTRUCTOR_ALLOWLIST:
                continue
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(
                        node.func.id,
                        STORE_CONSTRUCTORS,
                        f"{relative}:{node.lineno} opens a store; take it from the runtime",
                    )

    def test_orchestration_branching_only_shrinks(self) -> None:
        """The engine's rule, applied to the layers above it: ladders may only shrink."""
        package = Path(wayfarer.__file__).parent
        counted: dict[tuple[str, str], int] = {}
        for domain in ("orchestration", "transport"):
            for source in sorted((package / domain).rglob("*.py")):
                name = source.relative_to(package).as_posix()
                for node in ast.walk(ast.parse(source.read_text())):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        branches = sum(isinstance(n, ast.If) for n in ast.walk(node))
                        if branches > BRANCH_LIMIT:
                            counted[(name, node.name)] = max(
                                counted.get((name, node.name), 0), branches
                            )
        for key, branches in sorted(counted.items()):
            allowed = ORCHESTRATION_BRANCHING.get(key)
            with self.subTest(function=key):
                self.assertIsNotNone(
                    allowed,
                    f"{key[0]}:{key[1]} has {branches} branches; register a family instead",
                )
                assert allowed is not None
                self.assertLessEqual(branches, allowed, f"{key[0]}:{key[1]} grew a branch")
        for key, allowed in sorted(ORCHESTRATION_BRANCHING.items()):
            with self.subTest(function=key):
                self.assertLessEqual(
                    counted.get(key, 0),
                    allowed,
                    f"{key[0]}:{key[1]} is stale in ORCHESTRATION_BRANCHING",
                )

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


def test_prototype_resolver_and_legacy_paths_are_retired() -> None:
    """#634: the pre-runtime creation paths are deleted, not kept behind a flag."""
    from wayfarer import contracts, models

    package = Path(wayfarer.__file__).parent
    for relative in (
        "engine/simulation/resolution.py",
        "orchestration/service.py",
        "transport/http.py",
    ):
        assert not (package / relative).exists(), relative
    for module in (models, contracts):
        assert not hasattr(module, "Action") and not hasattr(module, "Event")
    assert set(contracts.CommandReceipt.__annotations__) == {"action", "outcome"}
    # The lobby is the only creation path; a studio may not write a starting snapshot.
    from wayfarer.orchestration.scenario_documents import ScenarioDocuments
    from wayfarer.orchestration.studio import ScenarioStudio

    for owner in (ScenarioStudio, ScenarioDocuments):
        assert not hasattr(owner, "activate"), owner.__name__


def test_persistence_declares_each_schema_once_and_reads_no_older_shape() -> None:
    """#634: no additive migration, no backfill, no reader for a retired schema."""
    import re

    from wayfarer.persistence import async_sqlite, events, postgres

    package = Path(wayfarer.__file__).parent
    retired = (
        "_ensure_stream",
        "_ensure_digests",
        "_import_narration",
        "upcast_command",
        "retire_transcript",
    )
    for module in (async_sqlite, postgres, events):
        for name in retired:
            assert not hasattr(module, name), f"{module.__name__}.{name}"
    for adapter in ("persistence/async_sqlite.py", "persistence/postgres.py"):
        source = (package / adapter).read_text()
        assert "ADD COLUMN" not in source, adapter
        # Columns and tables that existed only to carry an older database forward.
        for retired_column in ("engine_version", "state_after TEXT", "state_after JSONB"):
            assert retired_column not in source, (adapter, retired_column)
        assert "narration_migrations" not in source, adapter
        tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", source)
        assert len(tables) == len(set(tables)), (adapter, tables)


# Modules that still compare a campaign command kind against a string literal,
# with the number of registered kinds each names today. #635 moved dispatch into
# `orchestration/commands.py`; what is left is a family deciding inside itself,
# which #638 turns into a `CommandPlan`. An entry may shrink or disappear, it may
# never grow, and no new name may be added.
COMMAND_KIND_COMPARISONS: dict[str, int] = {
    "combat/preflight.py": 1,
    "director.py": 4,
    "noncombat.py": 4,
    "objectives.py": 2,
    "party.py": 13,
    "providers.py": 2,
    "recovery.py": 2,
}

# Function-local `wayfarer` imports left under `orchestration/`, with the count
# each module carries today. #635 promoted the two it names. The two left in
# `npcs.py` reach `play`, which imports `npcs` for its checkpoint hooks that #646
# moves into the engine; `party.py` and `recovery` flush each other. Shrink only:
# an entry may shrink or disappear, it may never grow, and no name may be added.
DEFERRED_ORCHESTRATION_IMPORTS: dict[str, int] = {"npcs.py": 2, "party.py": 1}


def _literals(node: ast.Compare) -> set[str]:
    values: set[str] = set()
    for operand in (node.left, *node.comparators):
        if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
            values.add(operand.value)
        elif isinstance(operand, ast.Tuple | ast.List | ast.Set):
            values.update(
                element.value
                for element in operand.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return values


def test_campaign_commands_are_dispatched_by_the_registry() -> None:
    """#635: the runtime looks a family up; it does not ask what kind a command is."""
    from wayfarer.orchestration import commands

    registered = set(commands.kinds()) | set(commands.UNGUARDED_ACTIONS)
    package = Path(wayfarer.__file__).parent / "orchestration"
    counts: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        if path.name == "commands.py":
            continue
        found = 0
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Compare):
                found += len(_literals(node) & registered)
        if found:
            counts[str(path.relative_to(package))] = found
    assert set(counts) <= set(COMMAND_KIND_COMPARISONS), sorted(
        set(counts) - set(COMMAND_KIND_COMPARISONS)
    )
    for name, allowed in COMMAND_KIND_COMPARISONS.items():
        assert counts.get(name, 0) <= allowed, (name, counts.get(name, 0), allowed)
    # The runtime itself names no kind at all.
    assert "runtime.py" not in counts


def test_deferred_orchestration_imports_only_shrink() -> None:
    """#635: the promoted deferrals stay promoted; the rest shrink toward none."""
    package = Path(wayfarer.__file__).parent / "orchestration"
    counts: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        inside = {
            node
            for parent in ast.walk(tree)
            if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef)
            for node in ast.walk(parent)
            if isinstance(node, ast.Import | ast.ImportFrom)
        }
        deferred = sum(
            1
            for node in inside
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("wayfarer")
            or isinstance(node, ast.Import)
            and any(alias.name.startswith("wayfarer") for alias in node.names)
        )
        if deferred:
            counts[str(path.relative_to(package))] = deferred
    assert set(counts) <= set(DEFERRED_ORCHESTRATION_IMPORTS), sorted(
        set(counts) - set(DEFERRED_ORCHESTRATION_IMPORTS)
    )
    for name, allowed in DEFERRED_ORCHESTRATION_IMPORTS.items():
        assert counts.get(name, 0) <= allowed, (name, counts.get(name, 0), allowed)


def test_command_registry_is_consistent() -> None:
    """#635: one family per kind, each with a receipt, an authorizer and its guards."""
    from wayfarer.contracts import EventAction
    from wayfarer.orchestration import commands
    from wayfarer.orchestration.providers import Intent

    actions = set(get_args(EventAction))
    seen: set[str] = set()
    for family in commands.FAMILIES:
        assert family.kinds, family
        assert not (seen & set(family.kinds)), sorted(seen & set(family.kinds))
        seen.update(family.kinds)
        assert family.receipt in actions, family.receipt
        assert callable(family.authorize) and callable(family.service)
        assert all(callable(rule) for rule in family.preconditions)
    assert commands.ACTIONS.receipt in actions
    # Every kind the model may propose resolves to a family, typed actions included.
    for kind in get_args(Intent.model_fields["kind"].annotation):
        assert commands.family_for(kind) is not None
    # The guard exemptions the ladder carried by name are now the families that
    # do not list `recovery_guard`.
    unguarded = {
        kind
        for family in commands.FAMILIES
        if commands.recovery_guard not in family.preconditions
        for kind in family.kinds
    } | set(commands.UNGUARDED_ACTIONS)
    assert unguarded == {
        "gurps_recovery",
        "care",
        "panic-response",
        "propose_fright_build",
        "approve_fright_build",
        "take_combat_turn",
        "take_unarmed_turn",
        "choose_defense",
        "resume_interrupted_turn",
    }


def test_genesis_is_written_only_by_commit_genesis() -> None:
    """#636: creating a campaign is one command, committed in one place."""
    package = Path(wayfarer.__file__).parent
    for adapter in ("persistence/async_sqlite.py", "persistence/postgres.py"):
        tree = ast.parse((package / adapter).read_text())
        writers = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and "INSERT INTO stream_genesis" in child.value
        }
        assert writers == {"commit_genesis"}, (adapter, sorted(writers))
    # Nothing above persistence inserts a campaign row of its own. `list.insert`
    # is a different verb, so only a call on something store-shaped counts.
    for layer in ("orchestration", "transport"):
        for source in sorted((package / layer).rglob("*.py")):
            for node in ast.walk(ast.parse(source.read_text())):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "insert"
                    and isinstance(node.func.value, ast.Attribute | ast.Name)
                ):
                    owner = node.func.value
                    name = owner.attr if isinstance(owner, ast.Attribute) else owner.id
                    assert name != "store", f"{source}:{node.lineno}"


# Transport modules that still reach past the runtime, with what each names today.
# #637 moved every campaign read behind the projection registry; the v1 modules
# keep their own ledger and lifecycle until #641 moves both. Shrink only.
TRANSPORT_REACH_ALLOWLIST = frozenset(
    {"v1/service.py", "v1/invitations.py", "v1/outbox.py", "v1/http.py"}
)
# Attribute names that mean a transport module is doing orchestration's work.
TRANSPORT_FORBIDDEN = frozenset({"_load", "stream_states", "reviewer"})


def test_transport_does_not_reach_past_the_runtime() -> None:
    """#637: a route asks the runtime for a projection; it does not run the engine."""
    package = Path(wayfarer.__file__).parent / "transport"
    for source in sorted(package.rglob("*.py")):
        relative = str(source.relative_to(package))
        if relative in TRANSPORT_REACH_ALLOWLIST:
            continue
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in TRANSPORT_FORBIDDEN, f"{relative}:{node.lineno}"
                # The campaign store is the runtime's; a catalog or job store is not.
                if node.attr == "store" and isinstance(node.value, ast.Attribute):
                    assert node.value.attr != "play", f"{relative}:{node.lineno}"
            if isinstance(node, ast.ImportFrom):
                assert "commit_command" not in {a.name for a in node.names}, relative
