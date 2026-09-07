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
    projects: list[str] = []

    def run(
        args: list[str], *, env: dict[str, str], check: bool
    ) -> subprocess.CompletedProcess[str]:
        project = next(a.split("=")[1] for a in args if a.startswith("--project="))
        projects.append(project)
        if project == "desktop" or not missing:
            error = "<failure message='broken'/>" if project == "phone" else ""
            Path(env["PLAYWRIGHT_JUNIT_OUTPUT_FILE"]).write_text(
                f'<testsuites><testsuite><testcase name="{project}">{error}'
                "</testcase></testsuite></testsuites>"
            )
        return subprocess.CompletedProcess(args, 1 if project == "phone" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    assert run_live_browsers.main() == 1
    assert projects == ["desktop", "phone"]
    cases = list(ET.parse(destination).getroot().iter("testcase"))
    assert len(cases) == 2
    assert cases[1].find("error" if missing else "failure") is not None
