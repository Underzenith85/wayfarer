"""Bounded real pytest/branch-coverage pipeline and tampered evidence refusal."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.aggregate_ci_shards import aggregate, validate_phases
from scripts.ci_shard_protocol import digest, identity, load
from scripts.plan_ci_shards import plan

ROOT = Path(__file__).resolve().parents[1]


def toy(root: Path) -> Path:
    (root / "src/wayfarer").mkdir(parents=True)
    (root / "src/wayfarer/__init__.py").write_text(
        "def branch(value):\n    if value:\n        return 1\n    return 0\n"
    )
    (root / "tests").mkdir()
    nodes: list[str] = []
    for i in range(4):
        (root / f"tests/test_{i}.py").write_text(
            'import pytest\nfrom wayfarer import branch\n@pytest.mark.parametrize("backend", ["sqlite", "postgres"])\ndef test_value(backend, subtests, record_property):\n    record_property("checkout_digest", "sentinel")\n    for value in [False, True]:\n        with subtests.test(value=value):\n            assert branch(value) == int(value)\n'
        )
        file = root / f"tests/test_{i}.py"
        file.write_text(file.read_text() + f"\n# module {i}\n")
        nodes.extend(f"tests/test_{i}.py::test_value[{b}]" for b in ("sqlite", "postgres"))
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\npythonpath=["src"]\naddopts="--cov=wayfarer --cov-fail-under=65"\n[tool.coverage.run]\nbranch=true\n[tool.coverage.report]\n'
    )
    (root / "uv.lock").write_text("toy-lock\n")
    (root / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "toy",
        ],
        cwd=root,
        check=True,
    )
    manifest = plan(nodes, root, {})
    manifest["source"] = identity(root)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def execute(root: Path, manifest: Path) -> Path:
    inputs = root / "inputs"
    inputs.mkdir()
    for i in range(4):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/run_ci_shard.py"),
                str(manifest),
                str(i),
                "--output",
                str(inputs / f"shard-{i}"),
            ],
            cwd=root,
            env=dict(
                os.environ,
                PYTHONPATH=str(ROOT),
                WAYFARER_TEST_DATABASE_URL="postgresql://isolated-test",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return inputs


def test_real_bounded_pytest_and_branch_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = toy(tmp_path)
    inputs = execute(tmp_path, path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    aggregate(path, inputs, tmp_path / "combined")
    import xml.etree.ElementTree as ET

    tree = ET.parse(tmp_path / "combined/pytest.xml")
    assert len(list(tree.iter("testcase"))) == 8
    assert sum(int(s.get("tests", "0")) for s in tree.getroot()) == 24
    assert (tmp_path / "combined/coverage.xml").is_file()
    assert (
        len(
            [
                p
                for p in tree.iter("property")
                if p.get("name") == "checkout_digest" and p.get("value") == "sentinel"
            ]
        )
        == 8
    )
    for i in range(4):
        phases = load(inputs / f"shard-{i}/accounting.json")["phases"]
        assert isinstance(phases, list) and len(phases) == 10


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "failed",
        "head",
        "duplicate",
        "unexpected",
        "pg-skip",
        "bad-phase",
        "hash",
        "no-branch",
    ],
)
def test_aggregate_refuses_incomplete_or_tampered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path = toy(tmp_path)
    inputs = execute(tmp_path, path)
    folder = inputs / "shard-0"
    raw = load(folder / "accounting.json")
    if change == "missing":
        folder.rename(inputs / "wrong-shard")
    elif change == "failed":
        raw["exit_code"] = 1
    elif change == "head":
        raw["source"] = {}
    elif change == "duplicate":
        raw["collected"] = ["duplicate", "duplicate"]
    elif change == "unexpected":
        raw["collected"] = ["tests/test_unexpected.py::test_x"]
    elif change == "hash":
        raw["coverage_sha256"] = "wrong"
    elif change == "no-branch":
        from coverage import CoverageData

        file = folder / ".coverage.0"
        file.unlink()
        data = CoverageData(basename=str(file))
        data.add_lines({"src/wayfarer/__init__.py": {1}})
        data.write()
        raw["coverage_sha256"] = digest(file)
    else:
        phases = raw["phases"]
        assert isinstance(phases, list)
        if change == "bad-phase":
            phases.append({})
        else:
            for row in phases:
                if "postgres" in row["node"] and row["when"] == "call":
                    row["outcome"] = "skipped"
    if change != "missing":
        (folder / "accounting.json").write_text(json.dumps(raw))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        aggregate(path, inputs, tmp_path / "combined")


def test_failure_subtest_and_duplicate_phases_refused() -> None:
    node = "tests/test_x.py::test_x[postgres]"
    phases = [
        {"node": node, "when": w, "kind": "parent", "outcome": "passed"}
        for w in ("setup", "call", "teardown")
    ]
    validate_phases(phases, (node,))
    for extra in (
        {
            "node": node,
            "when": "call",
            "kind": "subtest",
            "outcome": "failed",
            "context": {"msg": None, "kwargs": {}},
        },
        phases[1],
    ):
        with pytest.raises(ValueError):
            validate_phases([*phases, extra], (node,))


def test_aggregate_enforces_combined_coverage_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coverage import CoverageData

    path = toy(tmp_path)
    inputs = execute(tmp_path, path)
    for i in range(4):
        folder = inputs / f"shard-{i}"
        file = folder / f".coverage.{i}"
        file.unlink()
        data = CoverageData(basename=str(file))
        data.add_arcs({"src/wayfarer/__init__.py": {(-1, 1), (1, -1)}})
        data.write()
        raw = load(folder / "accounting.json")
        raw["coverage_sha256"] = digest(file)
        (folder / "accounting.json").write_text(json.dumps(raw))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    with pytest.raises(subprocess.CalledProcessError):
        aggregate(path, inputs, tmp_path / "combined")
    assert (tmp_path / "combined/pytest.xml").is_file()


def test_identity_refuses_dirty_tracked_source(tmp_path: Path) -> None:
    toy(tmp_path)
    (tmp_path / "src/wayfarer/__init__.py").write_text("changed")
    with pytest.raises(ValueError, match="tracked source"):
        identity(tmp_path)


def test_class_junit_identity() -> None:
    import xml.etree.ElementTree as ET

    from scripts.aggregate_ci_shards import case_node

    case = ET.fromstring('<testcase classname="tests.test_x.TestFamily" name="test_y[postgres]"/>')
    assert case_node(case) == "tests/test_x.py::TestFamily::test_y[postgres]"


@pytest.mark.parametrize("when", ["setup", "teardown"])
def test_subtest_requires_call_phase(when: str) -> None:
    node = "tests/test_x.py::test_x"
    with pytest.raises(ValueError):
        validate_phases(
            [
                {
                    "node": node,
                    "when": when,
                    "kind": "subtest",
                    "outcome": "passed",
                    "context": {"msg": None, "kwargs": {}},
                }
            ],
            (node,),
        )


def test_workflow_final_guard_and_original_gate_dependencies() -> None:
    text = (ROOT / ".github/workflows/package.yml").read_text()
    package = text.split("  package:\n", 1)[1]
    assert "needs: [plan, tests, quality, feedback]" in package and "if: always()" in package
    assert 'test "$PLAN_RESULT" = success && test "$TEST_RESULT" = success' in package
    assert 'python: ["3.14"]' in package
    for gate in (
        "ruff check .",
        "ruff format --check .",
        "mypy",
        "check_no_any.py",
        "check_quality_gates.py",
        "audit_gurps_sources.py",
        "audit_gurps_equipment.py",
        "validate_contracts.py",
        "scripts.validate_live_contracts",
        "scripts.aggregate_ci_shards",
        "certify_gurps_basic_set.py",
        "scripts.release_gates",
        "uv build",
        "uv pip install",
        "smoke_installed.py",
    ):
        assert gate in text
    assert "--require-certified" in package and "--gurps-basic-set" in package
    assert "include-hidden-files: true" in text and "fail-fast: false" in text


@pytest.mark.parametrize("failure", ["setup", "subtest", "pg-skip"])
def test_real_failure_evidence_retained(tmp_path: Path, failure: str) -> None:
    import xml.etree.ElementTree as ET

    path = toy(tmp_path)
    manifest = load(path)
    groups = manifest["shards"]
    assert isinstance(groups, list)
    module = groups[0]["modules"][0]
    file = tmp_path / module
    text = file.read_text()
    if failure == "subtest":
        text = text.replace(
            "assert branch(value) == int(value)", "assert False, 'causal subtest failure'"
        )
    elif failure == "setup":
        text += "\n@pytest.fixture(autouse=True)\ndef unavailable():\n    raise RuntimeError('causal setup failure')\n"
    else:
        text = text.replace(
            '    record_property("checkout_digest", "sentinel")',
            '    if backend == "postgres":\n        pytest.skip("causal PostgreSQL skip")\n    record_property("checkout_digest", "sentinel")',
        )
    file.write_text(text)
    subprocess.run(["git", "add", module], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "failure",
        ],
        cwd=tmp_path,
        check=True,
    )
    manifest["source"] = identity(tmp_path)
    path.write_text(json.dumps(manifest))
    output = tmp_path / "evidence"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_ci_shard", str(path), "0", "--output", str(output)],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONPATH=str(ROOT),
            WAYFARER_TEST_DATABASE_URL="postgresql://isolated-test",
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == (0 if failure == "pg-skip" else 1), result.stdout + result.stderr
    raw = load(output / "accounting.json")
    with pytest.raises(ValueError):
        validate_phases(raw["phases"], tuple(groups[0]["nodes"]))
    tree = ET.parse(output / "pytest.xml")
    assert list(
        tree.iter(
            "skipped" if failure == "pg-skip" else "error" if failure == "setup" else "failure"
        )
    )


def test_feedback_complete_git_status_inventory(tmp_path: Path) -> None:
    from scripts.run_ci_feedback import changes

    toy(tmp_path)
    base = identity(tmp_path)["head"]
    (tmp_path / "tests/test_0.py").write_text("# modified\n")
    (tmp_path / "tests/test_1.py").unlink()
    (tmp_path / "tests/test_2.py").rename(tmp_path / "tests/test_renamed.py")
    (tmp_path / "tests/test_added.py").write_text("# new\n")
    subprocess.run(["git", "add", "tests"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "statuses",
        ],
        cwd=tmp_path,
        check=True,
    )
    rows = changes(tmp_path, base)
    assert {r.status for r in rows} == {"M", "D", "A", "R100"}
    assert next(r for r in rows if r.status == "R100").previous_path == "tests/test_2.py"
    with pytest.raises(ValueError):
        changes(tmp_path, "HEAD; invalid")


def test_feedback_unknown_base_awaits_full_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.run_ci_feedback import run

    toy(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert run("", tmp_path / "feedback") == 0
    row = load(tmp_path / "feedback/selection.json")
    assert row["mode"] == "full-required"
    assert not (tmp_path / "feedback/pytest.xml").exists()


@pytest.mark.parametrize("skip_pg", [False, True])
def test_actual_selected_feedback_both_backends(tmp_path: Path, skip_pg: bool) -> None:
    toy(tmp_path)
    base = identity(tmp_path)["head"]
    file = tmp_path / "tests/test_0.py"
    text = file.read_text() + "\n# changed test-only behavior\n"
    if skip_pg:
        text = text.replace(
            '    record_property("checkout_digest", "sentinel")',
            '    if backend == "postgres":\n        pytest.skip("unavailable PG")\n    record_property("checkout_digest", "sentinel")',
        )
    file.write_text(text)
    subprocess.run(["git", "add", "tests/test_0.py"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "feedback",
        ],
        cwd=tmp_path,
        check=True,
    )
    output = tmp_path / "feedback"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_ci_feedback", "--base", base, "--output", str(output)],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONPATH=str(ROOT),
            WAYFARER_TEST_DATABASE_URL="postgresql://isolated-test",
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == int(skip_pg), result.stdout + result.stderr
    selected = load(output / "selection.json")
    assert selected["mode"] == "selected" and selected["selected_modules"] == ["tests/test_0.py"]
    import xml.etree.ElementTree as ET

    cases = list(ET.parse(output / "pytest.xml").iter("testcase"))
    assert {c.get("name") for c in cases} == {"test_value[sqlite]", "test_value[postgres]"}


def test_feedback_refuses_partial_module_collection_skip(tmp_path: Path) -> None:
    toy(tmp_path)
    base = identity(tmp_path)["head"]
    (tmp_path / "tests/test_0.py").write_text(
        'import pytest\npytest.skip("collection unavailable", allow_module_level=True)\n'
    )
    file = tmp_path / "tests/test_1.py"
    file.write_text(file.read_text() + "\n# selected passing module\n")
    subprocess.run(["git", "add", "tests"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CI Test",
            "-c",
            "user.email=ci@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "collection skip",
        ],
        cwd=tmp_path,
        check=True,
    )
    output = tmp_path / "feedback"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_ci_feedback", "--base", base, "--output", str(output)],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONPATH=str(ROOT),
            WAYFARER_TEST_DATABASE_URL="postgresql://isolated-test",
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    selected = load(output / "selection.json")
    assert selected["mode"] == "selected" and selected["selected_modules"] == [
        "tests/test_0.py",
        "tests/test_1.py",
    ]


def test_complete_collection_refuses_module_level_skip(tmp_path: Path) -> None:
    toy(tmp_path)
    (tmp_path / "tests/test_0.py").write_text(
        'import pytest\npytest.skip("missing suite", allow_module_level=True)\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.plan_ci_shards", "--collect-inventory"],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "collection failed or skipped" in result.stderr


def test_feedback_changed_full_six_module_inventory_awaits_existing_gate(tmp_path: Path) -> None:
    toy(tmp_path)
    for i in (4, 5):
        (tmp_path / f"tests/test_{i}.py").write_text((tmp_path / "tests/test_0.py").read_text())
    subprocess.run(["git", "add", "tests"], cwd=tmp_path, check=True)
    command = [
        "git",
        "-c",
        "user.name=CI Test",
        "-c",
        "user.email=ci@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
    ]
    subprocess.run([*command, "six-module baseline"], cwd=tmp_path, check=True)
    base = identity(tmp_path)["head"]
    for file in sorted((tmp_path / "tests").glob("test_*.py")):
        file.write_text(file.read_text() + "\n# modified suite\n")
    subprocess.run(["git", "add", "tests"], cwd=tmp_path, check=True)
    subprocess.run([*command, "all modules modified"], cwd=tmp_path, check=True)
    output = tmp_path / "feedback"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_ci_feedback", "--base", base, "--output", str(output)],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONPATH=str(ROOT),
            WAYFARER_TEST_DATABASE_URL="postgresql://isolated-test",
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = load(output / "selection.json")
    assert selected["mode"] == "full-required" and selected["selected_modules"] == []
    reasons = selected["reasons"]
    assert isinstance(reasons, list) and "feedback_budget_exceeded:max_modules=5" in reasons
    assert not (output / "pytest.xml").exists()
