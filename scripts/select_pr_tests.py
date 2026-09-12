"""Select the Python tests affected by a pull request.

The selector follows static Python imports from changed production modules to
their test consumers.  Ambiguous, shared, or non-Python changes deliberately
fall back to the complete suite.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PYTHON_PREFIXES = ("src/", "tests/", "scripts/")
FRONTEND_PREFIXES = ("frontend/", "contracts/")
FULL_PYTHON_PATHS = {
    ".python-version",
    "pyproject.toml",
    "server.py",
    "tests/conftest.py",
    "uv.lock",
}
FULL_PYTHON_PREFIXES = (".github/workflows/", "contracts/", "scripts/")
INVARIANT_TESTS = (
    "tests/test_architecture.py",
    "tests/test_command_entropy.py",
    "tests/test_contracts.py",
    "tests/test_profiles.py",
    "tests/test_release_invariants.py",
    "tests/test_replay.py",
)


@dataclass(frozen=True)
class Selection:
    python_changed: bool
    frontend_changed: bool
    mode: str
    tests: tuple[str, ...]
    reason: str


def _module_name(path: Path, root: Path) -> str | None:
    relative = path.relative_to(root).as_posix()
    if relative.startswith("src/"):
        relative = relative.removeprefix("src/")
    elif not relative.startswith("tests/"):
        return None
    if not relative.endswith(".py"):
        return None
    parts = relative.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, module: str) -> set[str]:
    try:
        tree = ast.parse(path.read_text())
    except OSError, SyntaxError, UnicodeDecodeError:
        return set()
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package_parts = package.split(".") if package else []
                keep = max(0, len(package_parts) - node.level + 1)
                prefix = ".".join(package_parts[:keep])
                base = ".".join(part for part in (prefix, node.module or "") if part)
            else:
                base = node.module or ""
            if base:
                found.add(base)
                found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def _python_graph(root: Path) -> tuple[dict[str, Path], dict[str, set[str]]]:
    modules: dict[str, Path] = {}
    for directory in (root / "src", root / "tests"):
        if directory.is_dir():
            for path in directory.rglob("*.py"):
                if name := _module_name(path, root):
                    modules[name] = path
    reverse: dict[str, set[str]] = defaultdict(set)
    for module, path in modules.items():
        for dependency in _imports(path, module):
            if dependency in modules:
                reverse[dependency].add(module)
    return modules, reverse


def _dependent_tests(changed: Iterable[Path], root: Path) -> set[str]:
    modules, reverse = _python_graph(root)
    changed_modules = {name for path in changed if (name := _module_name(path, root)) is not None}
    queue = deque(changed_modules)
    reached = set(changed_modules)
    while queue:
        for consumer in reverse.get(queue.popleft(), set()):
            if consumer not in reached:
                reached.add(consumer)
                queue.append(consumer)
    return {
        modules[module].relative_to(root).as_posix()
        for module in reached
        if module.startswith("tests.test_") and module in modules
    }


def select(changed_files: Iterable[str], root: Path) -> Selection:
    changed = tuple(
        sorted({path.strip().removeprefix("./") for path in changed_files if path.strip()})
    )
    workflow_changed = any(path.startswith(".github/workflows/") for path in changed)
    python_changed = any(
        path.startswith(PYTHON_PREFIXES)
        or path.startswith(FULL_PYTHON_PREFIXES)
        or path in FULL_PYTHON_PATHS
        for path in changed
    )
    frontend_changed = workflow_changed or any(
        path.startswith(FRONTEND_PREFIXES) for path in changed
    )
    if not python_changed:
        return Selection(False, frontend_changed, "none", (), "no Python-impacting files changed")

    full_reason = next(
        (
            path
            for path in changed
            if path in FULL_PYTHON_PATHS
            or path.startswith(FULL_PYTHON_PREFIXES)
            or (path.startswith("src/") and not path.endswith(".py"))
        ),
        None,
    )
    if full_reason:
        return Selection(True, frontend_changed, "full", (), f"shared path changed: {full_reason}")

    changed_paths = [root / path for path in changed if (root / path).is_file()]
    tests = _dependent_tests(changed_paths, root)
    tests.update(
        path
        for path in changed
        if path.startswith("tests/test_") and path.endswith(".py") and (root / path).is_file()
    )
    tests.update(path for path in INVARIANT_TESTS if (root / path).is_file())
    if len(tests) == len([path for path in INVARIANT_TESTS if (root / path).is_file()]):
        return Selection(
            True,
            frontend_changed,
            "full",
            (),
            "no static test dependency was found for a changed Python module",
        )
    return Selection(
        True, frontend_changed, "focused", tuple(sorted(tests)), "static dependants plus invariants"
    )


def _git_changes(root: Path, base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _write_github_output(path: Path, selection: Selection) -> None:
    values = {
        "python_changed": str(selection.python_changed).lower(),
        "frontend_changed": str(selection.frontend_changed).lower(),
        "python_mode": selection.mode,
        "python_tests": " ".join(selection.tests),
        "selection_reason": selection.reason,
    }
    with path.open("a") as output:
        for key, value in values.items():
            print(f"{key}={value}", file=output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    selection = select(_git_changes(root, args.base, args.head), root)
    if args.github_output:
        _write_github_output(args.github_output, selection)
    print(f"Python selection: {selection.mode} ({selection.reason})")
    for test in selection.tests:
        print(test)


if __name__ == "__main__":
    main()
