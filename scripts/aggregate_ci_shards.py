"""Fail closed on incomplete shard evidence; combine original branch coverage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from coverage import CoverageData

from scripts.ci_shard_protocol import (
    OPTIONAL_SKIP,
    coverage_config,
    digest,
    integer,
    load,
    manifest,
    mapping,
    strings,
)


def case_node(case: ET.Element) -> str:
    parts = case.get("classname", "").split(".")
    indices = [i for i, part in enumerate(parts) if part.startswith("test_")]
    if not indices or not case.get("name"):
        raise ValueError("JUnit missing parent identity")
    i = indices[0]
    return "/".join(parts[: i + 1]) + ".py::" + "::".join(parts[i + 1 :] + [case.get("name", "")])


def validate_phases(raw: object, expected: tuple[str, ...]) -> None:
    if not isinstance(raw, list):
        raise ValueError("missing execution phases")
    observed: dict[str, list[str]] = {}
    for value in raw:
        row = mapping(value)
        keys = {"node", "when", "outcome", "kind"}
        if row.get("kind") == "subtest":
            keys.add("context")
        if set(row) != keys:
            raise ValueError("malformed execution phase")
        node, when, outcome, kind = (row[k] for k in ("node", "when", "outcome", "kind"))
        if (
            not isinstance(node, str)
            or node not in expected
            or when not in ("setup", "call", "teardown")
            or kind not in ("parent", "subtest")
        ):
            raise ValueError("unexpected execution phase")
        if kind == "subtest":
            context = mapping(row["context"])
            if when != "call" or set(context) != {"msg", "kwargs"}:
                raise ValueError("malformed subtest context")
            if context["msg"] is not None and not isinstance(context["msg"], str):
                raise ValueError("malformed subtest message")
            if any(not isinstance(v, str) for v in mapping(context["kwargs"]).values()):
                raise ValueError("malformed subtest arguments")
        if outcome not in ("passed", "skipped") or (
            outcome == "skipped" and (node != OPTIONAL_SKIP or kind != "parent")
        ):
            raise ValueError("failed or skipped acceptance evidence")
        if kind == "parent":
            observed.setdefault(node, []).append(str(when))
    if set(observed) != set(expected):
        raise ValueError("missing parent execution")
    for node, phases in observed.items():
        wanted = (
            ["setup", "teardown"]
            if node == OPTIONAL_SKIP and "call" not in phases
            else ["setup", "call", "teardown"]
        )
        if phases != wanted:
            raise ValueError("duplicate or missing execution phases")


def aggregate(manifest_path: Path, inputs: Path, output: Path) -> None:
    source, shards = manifest(manifest_path, Path.cwd())
    folders = sorted(p.name for p in inputs.iterdir())
    if folders != [f"shard-{i}" for i in range(4)]:
        raise ValueError("missing or unexpected shard artifacts")
    output.mkdir(parents=True, exist_ok=False)
    combined = ET.Element("testsuites")
    expected_subtests = 0
    expected_skips = 0
    coverage_files: list[str] = []
    config_text = coverage_config(Path.cwd())
    for shard in shards:
        folder = inputs / f"shard-{shard.index}"
        raw = load(folder / "accounting.json")
        if set(raw) != {
            "version",
            "source",
            "manifest_sha256",
            "index",
            "exit_code",
            "collected",
            "phases",
            "junit_sha256",
            "coverage_sha256",
            "coverage_config_sha256",
        }:
            raise ValueError("malformed accounting")
        if (
            integer(raw["version"]) != 1
            or integer(raw["index"]) != shard.index
            or integer(raw["exit_code"]) != 0
        ):
            raise ValueError("failed shard")
        if raw["source"] != source or raw["manifest_sha256"] != digest(manifest_path):
            raise ValueError("source/manifest mismatch")
        if sorted(strings(raw["collected"])) != sorted(shard.nodes):
            raise ValueError("actual collection mismatch")
        validate_phases(raw["phases"], shard.nodes)
        phases = raw["phases"]
        assert isinstance(phases, list)
        for phase in phases:
            row = mapping(phase)
            expected_subtests += row["kind"] == "subtest"
            expected_skips += row["outcome"] == "skipped"
        junit, coverage, config = (
            folder / "pytest.xml",
            folder / f".coverage.{shard.index}",
            folder / "coverage-config.toml",
        )
        for key, path in (
            ("junit_sha256", junit),
            ("coverage_sha256", coverage),
            ("coverage_config_sha256", config),
        ):
            if not path.is_file() or raw[key] != digest(path):
                raise ValueError("artifact digest mismatch")
        if config.read_text() != config_text:
            raise ValueError("coverage configuration mismatch")
        data = CoverageData(basename=str(coverage))
        data.read()
        if not data.has_arcs() or not data.measured_files():
            raise ValueError("missing raw branch coverage")
        if any(Path(p).is_absolute() or ".." in Path(p).parts for p in data.measured_files()):
            raise ValueError("nonportable coverage path")
        tree = ET.parse(junit).getroot()
        if tree.tag != "testsuites" or not list(tree) or any(s.tag != "testsuite" for s in tree):
            raise ValueError("malformed JUnit suites")
        cases = list(tree.iter("testcase"))
        if sorted(case_node(c) for c in cases) != sorted(shard.nodes):
            raise ValueError("JUnit parent inventory mismatch")
        for case in cases:
            if list(case.iter("failure")) or list(case.iter("error")):
                raise ValueError("JUnit failure evidence")
            if list(case.iter("skipped")) and case_node(case) != OPTIONAL_SKIP:
                raise ValueError("skipped JUnit evidence")
        for suite in tree:
            # Preserve pytest's counters including dynamic subtest successes.
            if (
                integer(int(suite.get("failures", "-1"))) != 0
                or int(suite.get("errors", "-1")) != 0
                or int(suite.get("tests", "-1")) < len(list(suite.iter("testcase")))
            ):
                raise ValueError("inconsistent JUnit counters")
            combined.append(suite)
        coverage_files.append(str(coverage.resolve()))
    parent_nodes = [n for s in shards for n in s.nodes]
    if sum(int(s.get("tests", "0")) for s in combined) != len(parent_nodes) + expected_subtests:
        raise ValueError("JUnit/subtest accounting mismatch")
    if sum(int(s.get("skipped", "0")) for s in combined) != expected_skips:
        raise ValueError("JUnit/skip accounting mismatch")
    # Recollect in a fresh interpreter so global test imports cannot affect identity.
    result = subprocess.check_output(
        [sys.executable, "-m", "scripts.plan_ci_shards", "--collect-inventory"], text=True
    )
    import json

    value: object = json.loads(result)
    if sorted(strings(value)) != sorted(parent_nodes):
        raise ValueError("manifest omits or adds complete-suite nodes")
    ET.ElementTree(combined).write(output / "pytest.xml", encoding="utf-8", xml_declaration=True)
    config = output / "coverage-config.toml"
    config.write_text(config_text)
    env = dict(os.environ, COVERAGE_FILE=str((output / ".coverage").resolve()))
    base = [sys.executable, "-m", "coverage"]
    subprocess.run(
        [*base, "combine", "--keep", f"--rcfile={config}", *coverage_files], env=env, check=True
    )
    subprocess.run([*base, "report", f"--rcfile={config}", "--fail-under=65"], env=env, check=True)
    subprocess.run(
        [*base, "xml", f"--rcfile={config}", "-o", str(output / "coverage.xml")],
        env=env,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    aggregate(args.manifest, args.inputs, args.output)


if __name__ == "__main__":
    main()
