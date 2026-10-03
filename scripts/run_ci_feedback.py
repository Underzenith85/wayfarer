"""Additive selected-module feedback; fallback never launches a second full suite."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts.ci_shard_protocol import OPTIONAL_SKIP, identity
from scripts.select_ci_tests import Change, Manifest, select


def change_inventory(root: Path, base: str) -> tuple[tuple[Change, ...], str]:
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("missing or invalid exact base commit")
    common = subprocess.check_output(
        ["git", "merge-base", base, "HEAD"], cwd=root, text=True
    ).strip()
    data = (
        subprocess.check_output(
            ["git", "diff", "--name-status", "--find-renames", "-z", common, "HEAD"], cwd=root
        )
        .decode()
        .split("\0")
    )
    if data.pop() != "":
        raise ValueError("unterminated changed-file inventory")
    result: list[Change] = []
    while data:
        status = data.pop(0)
        if not data or not status:
            raise ValueError("malformed changed-file inventory")
        previous = data.pop(0)
        if status.startswith(("R", "C")):
            if not data:
                raise ValueError("missing rename/copy destination")
            result.append(Change(status, data.pop(0), previous))
        else:
            result.append(Change(status, previous))
    return tuple(result), common


def changes(root: Path, base: str) -> tuple[Change, ...]:
    return change_inventory(root, base)[0]


MAX_FEEDBACK_MODULES = 5


def limit_feedback(chosen: Manifest) -> Manifest:
    if chosen.mode == "selected" and len(chosen.selected_modules) > MAX_FEEDBACK_MODULES:
        return Manifest(
            "full-required",
            (),
            (*chosen.reasons, "feedback_budget_exceeded:max_modules=5"),
            chosen.changes,
            chosen.audited_exceptions,
        )
    return chosen


def run(base: str, output: Path) -> int:
    root = Path.cwd()
    source = identity(root)
    output.mkdir(parents=True, exist_ok=False)
    common: str | None = None
    try:
        changed, common = change_inventory(root, base)
        chosen = select(root, changed)
    except (ValueError, subprocess.CalledProcessError, UnicodeError) as exc:
        chosen = Manifest("full-required", (), ("base_diff_unavailable:" + str(exc),), ())
    chosen = limit_feedback(chosen)
    document = chosen.document()
    document["max_feedback_modules"] = MAX_FEEDBACK_MODULES
    document["source"] = source
    document["diff_provenance"] = {
        "requested_base_sha": base,
        "merge_base_sha": common,
        "head_sha": source["head"],
        "range": "merge-base-to-head",
    }
    (output / "selection.json").write_text(json.dumps(document, indent=2) + "\n")
    if chosen.mode == "full-required":
        print("Await full-suite shards; no duplicate full suite launched.")
        return 0
    if not chosen.selected_modules or not os.environ.get("WAYFARER_TEST_DATABASE_URL"):
        raise ValueError("selected feedback requires modules and PostgreSQL")
    skipped = False

    class NoSkippedEvidence:
        def pytest_collectreport(self, report: pytest.CollectReport) -> None:
            nonlocal skipped
            if report.skipped:
                skipped = True

        def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
            nonlocal skipped
            if report.skipped and report.nodeid != OPTIONAL_SKIP:
                skipped = True

    code = int(
        pytest.main(
            [
                "--no-cov",
                *chosen.selected_modules,
                "--durations=20",
                f"--junitxml={output / 'pytest.xml'}",
            ],
            plugins=[NoSkippedEvidence()],
        )
    )
    if identity(root) != source:
        raise ValueError("feedback source changed")
    return code or int(skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--output", type=Path, default=Path("feedback-artifacts"))
    args = parser.parse_args()
    raise SystemExit(run(args.base, args.output))


if __name__ == "__main__":
    main()
