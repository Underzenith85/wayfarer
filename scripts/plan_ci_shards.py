"""Full-suite planner: whole modules, exact parent-node partition."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import pytest


def collect_nodes() -> list[str]:
    """Collect the complete configured suite without executing tests."""
    nodes: list[str] = []
    skipped = False

    class Inventory:
        def pytest_collectreport(self, report: pytest.CollectReport) -> None:
            nonlocal skipped
            skipped = skipped or report.skipped

        def pytest_collection_finish(self, session: pytest.Session) -> None:
            nodes.extend(item.nodeid for item in session.items)

    with contextlib.redirect_stdout(io.StringIO()):
        status = pytest.main(["--collect-only", "--no-cov", "-q"], plugins=[Inventory()])
    if status != 0 or skipped:
        raise ValueError(f"complete collection failed or skipped: {status}")
    return nodes


def weights_from_junit(path: Path) -> dict[str, float]:
    """Parent testcase totals include setup/call/teardown and nested outcomes."""
    weights: dict[str, float] = {}
    for case in ET.parse(path).iter("testcase"):
        name = case.get("classname", "")
        if not name.startswith("tests."):
            raise ValueError("JUnit testcase lacks a tests module identity")
        parts = name.split(".")
        index = next((i for i, part in enumerate(parts) if part.startswith("test_")), None)
        if index is None:
            raise ValueError("JUnit module identity missing")
        module = "/".join(parts[: index + 1]) + ".py"
        seconds = float(case.get("time", "0"))
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid JUnit duration")
        weights[module] = weights.get(module, 0.0) + seconds
    return weights


def plan(
    nodes: list[str], root: Path, weights: dict[str, float], shards: int = 4
) -> dict[str, object]:
    if not nodes or len(nodes) != len(set(nodes)) or shards != 4:
        raise ValueError("require nonempty unique complete inventory and four shards")
    modules: dict[str, list[str]] = {}
    for node in nodes:
        module, separator, test = node.partition("::")
        p = PurePosixPath(module)
        if (
            not separator
            or not test
            or "\n" in node
            or "\\" in module
            or p.is_absolute()
            or ".." in p.parts
            or not module.startswith("tests/")
            or not p.name.startswith("test_")
            or p.suffix != ".py"
            or str(p) != module
            or not (root / module).is_file()
        ):
            raise ValueError(f"invalid or unknown test node: {node}")
        modules.setdefault(module, []).append(node)
    if any(not math.isfinite(w) or w < 0 for w in weights.values()):
        raise ValueError("invalid module duration")
    # Unknown modules get at least the largest measured module and one second/node.
    floor = max([60.0, *weights.values()])
    effective = {
        m: max(0.001, weights.get(m, max(floor, float(len(ns))))) for m, ns in modules.items()
    }
    bins: list[list[str]] = [[] for _ in range(shards)]
    totals = [0.0] * shards
    for module in sorted(modules, key=lambda m: (-effective[m], m)):
        slot = min(range(shards), key=lambda i: (totals[i], i))
        bins[slot].append(module)
        totals[slot] += effective[module]
    if not all(math.isfinite(total) for total in totals):
        raise ValueError("module weight totals overflow")
    assigned = [node for group in bins for module in group for node in modules[module]]
    if sorted(assigned) != sorted(nodes) or len(set(assigned)) != len(nodes):
        raise ValueError("partition does not match complete inventory")
    digest = hashlib.sha256("\n".join(sorted(nodes)).encode()).hexdigest()
    return {
        "version": 1,
        "inventory_sha256": digest,
        "parent_count": len(nodes),
        "unmeasured_weight_floor_seconds": floor,
        "shards": [
            {
                "index": i,
                "modules": sorted(group),
                "nodes": sorted(node for m in group for node in modules[m]),
                "modeled_seconds": totals[i],
            }
            for i, group in enumerate(bins)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--collect-inventory", action="store_true")
    args = parser.parse_args()
    if args.collect_inventory:
        print(json.dumps(collect_nodes()))
        return
    if args.output is None:
        parser.error("--output is required when planning")
    if args.inventory is None:
        nodes = collect_nodes()
    else:
        raw: object = json.loads(args.inventory.read_text())
        if not isinstance(raw, list) or any(not isinstance(n, str) for n in raw):
            raise ValueError("inventory must be a JSON string array")
        nodes = [str(n) for n in raw]
    from scripts.ci_shard_protocol import identity, load

    if args.junit is None:
        raw_weights = load(Path(__file__).with_name("ci_shard_timings.json"))["module_seconds"]
        weights = {}
        if not isinstance(raw_weights, dict):
            raise ValueError("invalid bundled timings")
        for key, value in raw_weights.items():
            if not isinstance(key, str) or type(value) not in (int, float):
                raise ValueError("invalid bundled weight")
            weights[key] = float(value)
    else:
        weights = weights_from_junit(args.junit)
    result = plan(nodes, Path.cwd(), weights)
    result["source"] = identity(Path.cwd())
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
