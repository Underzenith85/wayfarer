"""The shared release-evidence command must enforce Basic Set certification on request."""

import json
from pathlib import Path

import pytest

from scripts import release_gates


def test_release_gate_basic_set_flag_passes_and_publishes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def passing_evidence(report: Path) -> tuple[list[dict[str, object]], list[str]]:
        return [], []

    output = tmp_path / "release"
    monkeypatch.setattr(release_gates, "evaluate", passing_evidence)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_gates",
            "unused.xml",
            "--output",
            str(output),
            "--gurps-basic-set",
        ],
    )
    release_gates.main()
    payload = json.loads((output / "mechanics.json").read_text())
    assert payload["passed"] is True
    certification = payload["gurps_basic_set"]
    assert isinstance(certification, dict)
    assert certification["certified"] is True
    assert certification["profile_id"] == "profile:gurps-basic-set-4e-2004"
    assert certification["blockers"] == []
