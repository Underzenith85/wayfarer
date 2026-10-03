"""Conservative additive test-only feedback proposal; never replaces the full gate."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

VERSION = 1
# Audited production-only package imports, not dynamic test/fixture discovery.
AUDITED_DYNAMIC = {
    "tests/test_architecture.py": "f4adb653e33f24e5690342c798410a17ff804799ff2be8ca12b22039b490e4b0"
}


@dataclass(frozen=True)
class Change:
    status: str
    path: str
    previous_path: str | None = None


@dataclass(frozen=True)
class Manifest:
    mode: Literal["selected", "full-required"]
    selected_modules: tuple[str, ...]
    reasons: tuple[str, ...]
    changes: tuple[Change, ...]
    audited_exceptions: tuple[str, ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "selector_version": VERSION,
            "purpose": "additive_fast_feedback_only",
            "full_acceptance_gate_required": True,
            "mode": self.mode,
            "fallback": self.mode == "full-required",
            "reasons": list(self.reasons),
            "changes": [
                {"status": c.status, "path": c.path, "previous_path": c.previous_path}
                for c in self.changes
            ],
            "selected_modules": list(self.selected_modules),
            "pytest_arguments": (
                ["--no-cov", *self.selected_modules] if self.mode == "selected" else []
            ),
            "selected_execution_authorized": self.mode == "selected",
            "fallback_action": "await_existing_full_gate_do_not_launch_duplicate_full_suite",
            "backend_policy": "retain_all_parametrizations_require_postgresql_service_no_backend_filter",
            "audited_dynamic_exceptions": list(self.audited_exceptions),
        }


def valid_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not path.is_absolute()
        and value == path.as_posix()
        and not any(p in (".", "..") for p in path.parts)
        and "\\" not in value
        and "\x00" not in value
    )


def parse_changes(value: object) -> tuple[Change, ...]:
    if not isinstance(value, list):
        raise ValueError("changed-files must be a JSON array")
    result: list[Change] = []
    for item in value:
        if not isinstance(item, dict) or not set(item) <= {"status", "path", "previous_path"}:
            raise ValueError(
                "changed-files entries must contain status, path and optional previous_path"
            )
        status, path, previous = item.get("status"), item.get("path"), item.get("previous_path")
        if (
            not isinstance(status, str)
            or not isinstance(path, str)
            or (previous is not None and not isinstance(previous, str))
        ):
            raise ValueError("changed-files fields must be strings")
        if not valid_path(path) or (previous is not None and not valid_path(previous)):
            raise ValueError("ambiguous or unsafe changed path")
        result.append(Change(status, path, previous))
    if len({c.path for c in result}) != len(result):
        raise ValueError("duplicate changed paths")
    return tuple(sorted(result, key=lambda c: (c.path, c.status, c.previous_path or "")))


def module_name(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def graph(root: Path) -> tuple[dict[str, set[str]], tuple[str, ...], tuple[str, ...]]:
    files = sorted(
        p
        for directory in (root / "tests", root / "scripts")
        for p in directory.rglob("*.py")
        if p.is_file()
    )
    names: dict[str, str] = {}
    errors: list[str] = []
    audited: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        for name in (module_name(relative), module_name(relative).removeprefix("tests.")):
            if name in names and names[name] != relative:
                errors.append("ambiguous_test_import:" + name)
            names[name] = relative
    reverse: dict[str, set[str]] = {p.relative_to(root).as_posix(): set() for p in files}
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            errors.append("symlink_or_external_dependency:" + relative)
            continue
        try:
            source = path.read_bytes()
            tree = ast.parse(source, filename=relative)
        except (OSError, SyntaxError, ValueError) as exc:
            errors.append("unauditable_test:" + relative + ":" + type(exc).__name__)
            continue
        imports: set[str] = set()
        ambiguous = False
        unsafe_references = {
            "__import__",
            "__builtins__",
            "eval",
            "exec",
            "import_module",
            "exec_module",
            "load_module",
            "spec_from_file_location",
            "module_from_spec",
            "find_spec",
            "run_module",
            "run_path",
        }
        # Treat a dynamic-loader namespace itself as uncertain; passing or
        # rebinding it need not leave an obvious call at the import site.
        dynamic_namespaces = ("builtins", "importlib", "runpy")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
                if any(a.name.split(".")[0] in dynamic_namespaces for a in node.names):
                    ambiguous = True
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if base.split(".")[0] in dynamic_namespaces:
                    ambiguous = True
                if node.level:
                    package = module_name(relative).split(".")[:-1]
                    if node.level > len(package):
                        ambiguous = True
                        continue
                    base = ".".join(
                        package[: len(package) - node.level + 1] + ([base] if base else [])
                    )
                imports.add(base)
                imports.update(base + "." + a.name for a in node.names)
            elif isinstance(node, ast.Name) and node.id in unsafe_references:
                ambiguous = True
            elif isinstance(node, ast.Attribute) and node.attr in unsafe_references:
                ambiguous = True
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in ("globals", "locals"):
                    ambiguous = True
                elif (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) > 1
                ):
                    attribute = node.args[1]
                    if isinstance(attribute, ast.Constant) and attribute.value in unsafe_references:
                        ambiguous = True
            elif isinstance(node, ast.Subscript):
                if isinstance(node.slice, ast.Constant) and node.slice.value in unsafe_references:
                    ambiguous = True
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == "pytest_plugins" for t in targets):
                    ambiguous = True
        if ambiguous:
            if AUDITED_DYNAMIC.get(relative) == hashlib.sha256(source).hexdigest():
                audited.append(relative)
            else:
                errors.append("dynamic_test_dependency:" + relative)
        for name in imports:
            # Parent package imports can execute package initialization, too.
            parts = name.split(".")
            for n in range(1, len(parts) + 1):
                prefix = ".".join(parts[:n])
                dependency = names.get(prefix)
                if dependency is not None and dependency != relative:
                    reverse[dependency].add(relative)
            if name.startswith(("test_", "tests.", "support.", "scripts.")) and not any(
                name == known or name.startswith(known + ".") for known in names
            ):
                errors.append("unresolved_test_import:" + relative + ":" + name)
    return reverse, tuple(sorted(set(errors))), tuple(sorted(audited))


def select(root: Path, changes: tuple[Change, ...]) -> Manifest:
    changes = tuple(sorted(changes, key=lambda c: (c.path, c.status, c.previous_path or "")))
    reasons: list[str] = []
    if not changes:
        reasons.append("empty_change_set_has_no_execution_evidence")
    for change in changes:
        if not valid_path(change.path):
            reasons.append("unsafe_path:" + change.path)
        elif change.status != "M" or change.previous_path is not None:
            reasons.append("unreviewed_change_status:" + change.status + ":" + change.path)
        elif not (
            change.path.startswith("tests/")
            and Path(change.path).name.startswith("test_")
            and change.path.endswith(".py")
        ) or change.path.startswith("tests/support/"):
            reasons.append("shared_source_config_helper_or_unknown_path:" + change.path)
        elif (
            not (root / change.path).is_file()
            or (root / change.path).is_symlink()
            or not (root / change.path).resolve().is_relative_to(root.resolve())
        ):
            reasons.append("missing_or_symlink_test:" + change.path)
    if reasons:
        return Manifest("full-required", (), tuple(sorted(set(reasons))), changes)
    reverse, errors, audited = graph(root)
    if errors:
        return Manifest("full-required", (), errors, changes, audited)
    selected = {c.path for c in changes}
    frontier = list(sorted(selected))
    while frontier:
        current = frontier.pop()
        for dependent in sorted(reverse.get(current, ())):
            if dependent not in selected:
                selected.add(dependent)
                frontier.append(dependent)
    helpers = sorted(
        p for p in selected if not p.startswith("tests/") or not Path(p).name.startswith("test_")
    )
    if helpers:
        return Manifest(
            "full-required",
            (),
            tuple("reverse_dependency_shared_helper:" + p for p in helpers),
            changes,
            audited,
        )
    if not selected:
        return Manifest("full-required", (), ("empty_selection_refused",), changes, audited)
    return Manifest(
        "selected",
        tuple(sorted(selected)),
        ("modified_test_modules_and_transitive_reverse_import_consumers",),
        changes,
        audited,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-files",
        required=True,
        type=Path,
        help="JSON array of status/path/previous_path records; caller owns exact-base diff",
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        changes = parse_changes(json.loads(args.changed_files.read_text()))
        manifest = select(args.root.resolve(), changes)
    except (OSError, ValueError) as exc:
        manifest = Manifest("full-required", (), ("invalid_change_input:" + str(exc),), ())
    print(json.dumps(manifest.document(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
