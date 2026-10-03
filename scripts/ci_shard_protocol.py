"""Strict full-suite shard artifact protocol bound to the tested source."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

OPTIONAL_SKIP = "tests/test_codex_provider.py::test_authenticated_codex_smoke"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(root: Path) -> dict[str, str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    if git("diff", "HEAD", "--name-only"):
        raise ValueError("tracked source differs from HEAD")
    unknown = git(
        "ls-files", "--others", "--exclude-standard", "src", "tests", "scripts", "contracts"
    )
    if unknown:
        raise ValueError("uncommitted source files")
    return {
        "head": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "pyproject_sha256": digest(root / "pyproject.toml"),
        "lock_sha256": digest(root / "uv.lock"),
    }


def mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("expected object")
    return {str(k): v for k, v in value.items()}


def strings(value: object) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError("expected nonempty strings")
    return [str(v) for v in value]


def integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("expected integer")
    assert isinstance(value, int)
    return value


def load(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text())
    return mapping(value)


@dataclass(frozen=True)
class Shard:
    index: int
    modules: tuple[str, ...]
    nodes: tuple[str, ...]


def manifest(path: Path, root: Path) -> tuple[dict[str, str], list[Shard]]:
    from scripts.plan_ci_shards import plan

    raw = load(path)
    if integer(raw.get("version")) != 1:
        raise ValueError("unknown manifest version")
    source = mapping(raw.get("source"))
    current = identity(root)
    if source != current:
        raise ValueError("manifest source identity mismatch")
    groups = raw.get("shards")
    if not isinstance(groups, list) or len(groups) != 4:
        raise ValueError("require four shards")
    shards: list[Shard] = []
    for i, value in enumerate(groups):
        group = mapping(value)
        modules, nodes = strings(group.get("modules")), strings(group.get("nodes"))
        if integer(group.get("index")) != i or not nodes or len(modules) != len(set(modules)):
            raise ValueError("invalid shard assignment")
        if sorted(set(n.split("::", 1)[0] for n in nodes)) != sorted(modules):
            raise ValueError("module/node mismatch")
        shards.append(Shard(i, tuple(modules), tuple(nodes)))
    modules = [m for s in shards for m in s.modules]
    if len(modules) != len(set(modules)):
        raise ValueError("modules overlap across shards")
    nodes = [n for s in shards for n in s.nodes]
    checked = plan(nodes, root, {})
    if raw.get("inventory_sha256") != checked["inventory_sha256"] or raw.get("parent_count") != len(
        nodes
    ):
        raise ValueError("inventory digest/count mismatch")
    return current, shards


def coverage_config(root: Path) -> str:
    text = (root / "pyproject.toml").read_text()
    section = "[tool.coverage.run]\n"
    if text.count(section) != 1:
        raise ValueError("missing coverage configuration")
    # Preserve original branch/omit/report rules, adding only portable data paths.
    import tomllib

    data = tomllib.loads(text)["tool"]["coverage"]["run"]
    if "source" in data or "relative_files" in data or data.get("branch") is not True:
        raise ValueError("unsupported coverage configuration; review required")
    return text.replace(section, section + 'source = ["wayfarer"]\nrelative_files = true\n')
