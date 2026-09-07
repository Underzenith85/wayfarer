"""Combined browser evidence cannot hide a failed or missing viewport run."""

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import run_live_browsers


@pytest.mark.parametrize("missing", [False, True])
def test_both_viewports_run_and_failure_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: bool
) -> None:
    destination = tmp_path / "live.xml"
    monkeypatch.setenv("PLAYWRIGHT_JUNIT_OUTPUT_FILE", str(destination))
    monkeypatch.setattr(sys, "argv", ["runner"])
    runs: list[tuple[str, str]] = []
    outputs: set[str] = set()

    def run(
        args: list[str], *, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        project = next(a.split("=")[1] for a in args if a.startswith("--project="))
        batch = env["WAYFARER_LIVE_BATCH"]
        runs.append((project, batch))
        outputs.add(next(a for a in args if a.startswith("--output=")))
        failed = project == "phone" and batch == "workshop"
        if not failed or not missing:
            error = "<failure message='broken'/>" if failed else ""
            Path(env["PLAYWRIGHT_JUNIT_OUTPUT_FILE"]).write_text(
                f'<testsuites><testsuite><testcase name="{project}">{error}'
                "</testcase></testsuite></testsuites>"
            )
        return subprocess.CompletedProcess(args, 1 if failed else 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert run_live_browsers.main() == 1
    assert runs == [
        ("desktop", "regular"),
        ("desktop", "workshop"),
        ("phone", "regular"),
        ("phone", "workshop"),
    ]
    assert len(outputs) == 4
    cases = list(ET.parse(destination).getroot().iter("testcase"))
    assert len(cases) == 4
    assert cases[3].find("error" if missing else "failure") is not None


def test_single_viewport_still_isolates_both_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "live.xml"
    monkeypatch.setenv("PLAYWRIGHT_JUNIT_OUTPUT_FILE", str(destination))
    monkeypatch.setattr(sys, "argv", ["runner", "--project=desktop", "--workers=1"])
    batches: list[str] = []

    def run(
        args: list[str], *, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert args.count("--project=desktop") == 1 and "--workers=1" in args
        batches.append(env["WAYFARER_LIVE_BATCH"])
        Path(env["PLAYWRIGHT_JUNIT_OUTPUT_FILE"]).write_text(
            '<testsuites><testsuite><testcase name="passed"/></testsuite></testsuites>'
        )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert run_live_browsers.main() == 0
    assert batches == ["regular", "workshop"]
    assert len(list(ET.parse(destination).getroot().iter("testcase"))) == 2
