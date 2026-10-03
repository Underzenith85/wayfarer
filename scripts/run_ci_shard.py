"""Run one complete-suite partition and retain strict execution evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest
from _pytest.subtests import SubtestReport

from scripts.ci_shard_protocol import coverage_config, digest, identity, manifest


def run(manifest_path: Path, index: int, output: Path) -> int:
    root = Path.cwd()
    source, shards = manifest(manifest_path, root)
    if not 0 <= index < 4 or not os.environ.get("WAYFARER_TEST_DATABASE_URL"):
        raise ValueError("require valid shard and configured PostgreSQL service")
    shard = shards[index]
    output.mkdir(parents=True, exist_ok=False)
    config = output / "coverage-config.toml"
    config.write_text(coverage_config(root))
    phases: list[dict[str, object]] = []
    collected: list[str] = []

    class Accounting:
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            collected.extend(item.nodeid for item in session.items)
            if sorted(collected) != sorted(shard.nodes):
                raise pytest.UsageError("collection differs from assigned exact inventory")

        def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
            row: dict[str, object] = {
                "node": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
                "kind": "parent",
            }
            if isinstance(report, SubtestReport):
                row["kind"] = "subtest"
                row["context"] = {
                    "msg": report.context.msg,
                    "kwargs": {k: str(v) for k, v in report.context.kwargs.items()},
                }
            phases.append(row)

    previous = os.environ.get("COVERAGE_FILE")
    os.environ["COVERAGE_FILE"] = str((output / f".coverage.{index}").resolve())
    try:
        status = int(
            pytest.main(
                [
                    *shard.modules,
                    "--cov=wayfarer",
                    "--cov-branch",
                    f"--cov-config={config}",
                    "--cov-fail-under=0",
                    "--cov-report=",
                    "--durations=20",
                    f"--junitxml={output / 'pytest.xml'}",
                ],
                plugins=[Accounting()],
            )
        )
    finally:
        if previous is None:
            os.environ.pop("COVERAGE_FILE", None)
        else:
            os.environ["COVERAGE_FILE"] = previous
    if identity(root) != source:
        raise ValueError("source changed during shard execution")
    import json

    coverage = output / f".coverage.{index}"
    accounting = {
        "version": 1,
        "source": source,
        "manifest_sha256": digest(manifest_path),
        "index": index,
        "exit_code": status,
        "collected": collected,
        "phases": phases,
        "junit_sha256": digest(output / "pytest.xml"),
        "coverage_sha256": digest(coverage),
        "coverage_config_sha256": digest(config),
    }
    (output / "accounting.json").write_text(json.dumps(accounting, indent=2) + "\n")
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("index", type=int)
    parser.add_argument("--output", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    raise SystemExit(run(args.manifest, args.index, args.output))


if __name__ == "__main__":
    main()
