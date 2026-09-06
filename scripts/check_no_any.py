"""Reject explicit typing.Any in first-party Python code.

Mypy strict checks inferred Any. Pydantic Settings' generated constructor causes
mypy's disallow_any_explicit to flag the class declaration itself, so this narrow
source gate enforces the project policy without suppressing that dependency.
"""

import ast
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    failures: list[str] = []
    for parent in (root / "src", root / "tests", root / "scripts"):
        for path in parent.rglob("*.py"):
            tree = ast.parse(path.read_text())
            aliases: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "typing",
                    "typing_extensions",
                }:
                    aliases.update(
                        alias.asname or alias.name for alias in node.names if alias.name == "Any"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {"typing", "typing_extensions"}:
                            aliases.add((alias.asname or alias.name) + ".Any")
            if aliases:
                failures.append(f"{path.relative_to(root)} imports explicit Any: {sorted(aliases)}")
    if failures:
        raise SystemExit("\n".join(failures))
    print("No explicit Any imports in first-party Python")


if __name__ == "__main__":
    main()
