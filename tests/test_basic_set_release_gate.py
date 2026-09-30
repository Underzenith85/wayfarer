"""The shared release-evidence command must enforce Basic Set certification on request."""

import json
from pathlib import Path

import pytest

from scripts import release_gates


@pytest.mark.parametrize("report_only", [False, True])
def test_release_gate_blocks_certification_but_can_publish_build_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_only: bool
) -> None:
    def passing_evidence(report: Path) -> tuple[list[dict[str, object]], list[str]]:
        return [], []

    output = tmp_path / "release"
    revision = "a" * 40
    monkeypatch.setenv("GITHUB_SHA", revision)
    monkeypatch.setattr(release_gates, "evaluate", passing_evidence)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_gates",
            "unused.xml",
            "--output",
            str(output),
            "--basic-set-report-only" if report_only else "--gurps-basic-set",
        ],
    )
    if report_only:
        release_gates.main()
    else:
        with pytest.raises(SystemExit):
            release_gates.main()
    payload = json.loads((output / "mechanics.json").read_text())
    assert payload["passed"] is report_only
    certification = payload["gurps_basic_set"]
    assert isinstance(certification, dict)
    assert certification["certified"] is False
    assert certification["profile_id"] == "profile:gurps-basic-set-4e-2004"
    assert certification["repository_commit"] == revision
    assert certification["blockers"]
