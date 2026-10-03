"""Actual synthetic-merge mismatch cannot become passing release evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_ci_shard_execution import toy

from scripts.check_ci_release_provenance import check
from scripts.ci_shard_protocol import identity, load


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[Path, Path, str]:
    manifest = toy(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    head = identity(tmp_path)["head"]
    (reports / "gurps-basic-set.json").write_text(
        json.dumps({"repository_commit": head, "certified": False})
    )
    (reports / "mechanics.json").write_text(
        json.dumps(
            {
                "revision": head,
                "passed": True,
                "errors": [],
                "gurps_basic_set": {"repository_commit": head},
            }
        )
    )
    return manifest, reports, head


def test_actual_head_passes_with_ambient_synthetic_sha(
    tmp_path: Path, evidence: tuple[Path, Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, reports, head = evidence
    monkeypatch.setenv("GITHUB_SHA", "e78fdbf2a7c81301e0f39431e5998967645ce955")
    check(tmp_path, manifest, reports, head)


@pytest.mark.parametrize("field", ["basic", "mechanics", "nested"])
def test_synthetic_merge_report_refused(
    tmp_path: Path, evidence: tuple[Path, Path, str], field: str
) -> None:
    manifest, reports, head = evidence
    path = reports / ("gurps-basic-set.json" if field == "basic" else "mechanics.json")
    row = load(path)
    wrong = "e78fdbf2a7c81301e0f39431e5998967645ce955"
    if field == "nested":
        row["gurps_basic_set"] = {"repository_commit": wrong}
    else:
        row["repository_commit" if field == "basic" else "revision"] = wrong
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="report provenance"):
        check(tmp_path, manifest, reports, head)


@pytest.mark.parametrize("fault", ["dirty", "missing", "head", "tree", "failed", "malformed"])
def test_unverifiable_evidence_refused(
    tmp_path: Path, evidence: tuple[Path, Path, str], fault: str
) -> None:
    manifest, reports, head = evidence
    if fault == "dirty":
        (tmp_path / "src/wayfarer/__init__.py").write_text("dirty")
    elif fault == "missing":
        (reports / "mechanics.json").unlink()
    elif fault == "head":
        head = "0" * 40
    elif fault == "tree":
        row = load(manifest)
        source = row["source"]
        assert isinstance(source, dict)
        source["tree"] = "0" * 40
        manifest.write_text(json.dumps(row))
    elif fault == "failed":
        (reports / "mechanics.json").write_text(
            json.dumps(
                {
                    "revision": head,
                    "passed": False,
                    "errors": [],
                    "gurps_basic_set": {"repository_commit": head},
                }
            )
        )
    else:
        (reports / "gurps-basic-set.json").write_text("[]")
    with pytest.raises((ValueError, FileNotFoundError)):
        check(tmp_path, manifest, reports, head)


def test_workflow_overrides_report_process_sha_before_provenance_gate() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/package.yml").read_text()
    assert workflow.count('env GITHUB_SHA="$WAYFARER_CI_HEAD_SHA" uv run') == 2
    assert (
        "WAYFARER_CI_HEAD_SHA: ${{ github.event.pull_request.head.sha || github.sha }}" in workflow
    )
    package = workflow.split("  package:\n", 1)[1]
    gate = package.index("scripts.check_ci_release_provenance")
    assert (
        package.index("scripts.release_gates")
        < gate
        < package.index("name: engine-release-evidence")
    )
    assert gate < package.index("uv build")
