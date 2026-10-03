"""Policy oracles for the additive prototype, never acceptance-gate selection."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.select_ci_tests import Change, parse_changes, select


def tree(root: Path, **modules: str) -> None:
    for name, source in modules.items():
        path = root / "tests" / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


def test_reverse_fixture_consumers_union_cross_named_families(tmp_path: Path) -> None:
    tree(
        tmp_path,
        test_water="def fixture(): return 1\n",
        test_armoury="from test_water import fixture\n",
        test_haste="import test_armoury\n",
        test_other="def test_ok(): pass\n",
    )
    changes = parse_changes(
        [
            {"status": "M", "path": "tests/test_haste.py"},
            {"status": "M", "path": "tests/test_water.py"},
        ]
    )
    manifest = select(tmp_path, changes)
    assert manifest.mode == "selected"
    assert manifest.selected_modules == (
        "tests/test_armoury.py",
        "tests/test_haste.py",
        "tests/test_water.py",
    )
    assert manifest.document() == select(tmp_path, tuple(reversed(changes))).document()
    assert manifest.document()["full_acceptance_gate_required"] is True


@pytest.mark.parametrize(
    "status,path,previous",
    [
        ("M", "src/wayfarer/engine/simulation/magic/water_effects.py", None),
        ("M", "tests/support/runtime.py", None),
        ("M", "tests/helpers.py", None),
        ("M", "conftest.py", None),
        ("M", "pyproject.toml", None),
        ("M", ".github/workflows/package.yml", None),
        ("M", "contracts/v1/schemas.json", None),
        ("M", "scripts/replay_fixtures.py", None),
        ("M", "unknown.txt", None),
        ("A", "tests/test_new.py", None),
        ("D", "tests/test_water.py", None),
        ("R100", "tests/test_new.py", "tests/test_water.py"),
        ("M", "tests/test_new.py", None),
    ],
)
def test_unreviewed_paths_require_existing_full_gate(
    tmp_path: Path, status: str, path: str, previous: str | None
) -> None:
    tree(tmp_path, test_water="def test_ok(): pass\n")
    manifest = select(tmp_path, (Change(status, path, previous),))
    assert manifest.mode == "full-required" and not manifest.selected_modules
    assert manifest.reasons and manifest.document()["pytest_arguments"] == []
    assert (
        manifest.document()["fallback_action"]
        == "await_existing_full_gate_do_not_launch_duplicate_full_suite"
    )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [{"status": "M", "path": "../tests/test_a.py"}],
        [{"status": "M", "path": "/tests/test_a.py"}],
        [{"status": "M", "path": "tests//test_a.py"}],
        [{"status": 1, "path": "tests/test_a.py"}],
        [{"status": "M", "path": "tests/test_a.py", "unknown": True}],
        [{"status": "M", "path": "tests/test_a.py"}] * 2,
    ],
)
def test_ambiguous_change_input_refused(value: object) -> None:
    with pytest.raises(ValueError):
        parse_changes(value)


def test_empty_changes_cannot_report_selected_green(tmp_path: Path) -> None:
    assert select(tmp_path, ()).mode == "full-required"


@pytest.mark.parametrize(
    "source",
    [
        "import importlib\nimportlib.import_module(name)\n",
        "from importlib import import_module as load\nload(name)\n",
        "pytest_plugins = ['test_a']\n",
        "from test_missing import fixture\n",
        "def broken(:\n",
    ],
)
def test_unauditable_dependency_discovery_requires_full(tmp_path: Path, source: str) -> None:
    tree(tmp_path, test_a="def test_ok(): pass\n", test_b=source)
    assert select(tmp_path, (Change("M", "tests/test_a.py"),)).mode == "full-required"


def test_relative_import_and_shared_helper_reverse_join(tmp_path: Path) -> None:
    tree(
        tmp_path,
        test_a="def fixture(): return 1\n",
        helpers="from test_a import fixture\n",
        test_b="from helpers import fixture\n",
    )
    # Root-level helpers are local imports too: package-qualified imports make the dependency explicit.
    (tmp_path / "tests/helpers.py").write_text("from tests.test_a import fixture\n")
    manifest = select(tmp_path, (Change("M", "tests/test_a.py"),))
    assert manifest.mode == "full-required" and any("shared_helper" in r for r in manifest.reasons)
    tree(tmp_path, **{"pkg/__init__": "", "pkg/test_c": "from ..test_a import fixture\n"})
    (tmp_path / "tests/helpers.py").unlink()
    (tmp_path / "tests/test_b.py").unlink()
    assert (
        "tests/pkg/test_c.py"
        in select(tmp_path, (Change("M", "tests/test_a.py"),)).selected_modules
    )


def test_script_fixture_bridge_requires_full_not_missed_consumers(tmp_path: Path) -> None:
    tree(
        tmp_path, test_a="def fixture(): return 1\n", test_b="from scripts.bridge import fixture\n"
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/bridge.py").write_text("from test_a import fixture\n")
    manifest = select(tmp_path, (Change("M", "tests/test_a.py"),))
    assert manifest.mode == "full-required" and any(
        "scripts/bridge.py" in r for r in manifest.reasons
    )


def test_changed_audited_scanner_hash_requires_full(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    tree(
        tmp_path,
        test_a="def test_ok(): pass\n",
        test_architecture=(root / "tests/test_architecture.py").read_text(),
    )
    assert select(tmp_path, (Change("M", "tests/test_a.py"),)).mode == "selected"
    with (tmp_path / "tests/test_architecture.py").open("a") as stream:
        stream.write("\n# changed audited scanner\n")
    assert select(tmp_path, (Change("M", "tests/test_a.py"),)).mode == "full-required"


def collect(root: Path, modules: tuple[str, ...]) -> tuple[str, ...]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--no-cov", "-q", *modules],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return tuple(sorted(line for line in result.stdout.splitlines() if "::" in line))


def test_full_module_selection_retains_all_backend_node_ids(tmp_path: Path) -> None:
    tree(
        tmp_path,
        test_water="import pytest\n@pytest.fixture\ndef material(): return 1\n@pytest.mark.parametrize('backend',['sqlite','postgres'])\ndef test_water(backend, material): assert material == 1\n",
        test_armoury="import pytest\nfrom test_water import material\n@pytest.mark.parametrize('backend',['sqlite','postgres'])\n@pytest.mark.parametrize('kind',['weapon','armor'])\ndef test_consumer(backend,kind,material): assert material == 1\n",
        test_unrelated="def test_ok(): pass\n",
    )
    manifest = select(tmp_path, (Change("M", "tests/test_water.py"),))
    selected = collect(tmp_path, manifest.selected_modules)
    baseline = collect(tmp_path, ("tests",))
    expected = tuple(
        n for n in baseline if any(n.startswith(p + "::") for p in manifest.selected_modules)
    )
    assert selected == expected and len(selected) == 6
    assert sum("postgres" in n for n in selected) == 3 and sum("sqlite" in n for n in selected) == 3
    arguments = manifest.document()["pytest_arguments"]
    assert isinstance(arguments, list)
    assert not any("-k" in str(a) or "-m" == a for a in arguments)


def test_cli_malformed_input_emits_full_required_json(tmp_path: Path) -> None:
    changed = tmp_path / "changes.json"
    changed.write_text("not json")
    script = Path(__file__).resolve().parents[1] / "scripts/select_ci_tests.py"
    result = subprocess.run(
        [sys.executable, str(script), "--changed-files", str(changed), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    manifest = json.loads(result.stdout)
    assert manifest["mode"] == "full-required" and manifest["fallback"] and manifest["reasons"]


def test_symlink_dependency_and_selected_path_fail_closed(tmp_path: Path) -> None:
    tree(tmp_path, test_a="def test_ok(): pass\n")
    external = tmp_path / "outside.py"
    external.write_text("def test_bad(): pass\n")
    (tmp_path / "tests/test_link.py").symlink_to(external)
    for path in ("tests/test_a.py", "tests/test_link.py"):
        assert select(tmp_path, (Change("M", path),)).mode == "full-required"


@pytest.mark.parametrize(
    "consumer",
    [
        "import builtins as b\nb.__import__('test_fixture')\n",
        "loader=__import__\nloader('test_fixture')\n",
        "import builtins as b\nloader=b.__import__\nloader('test_fixture')\n",
        "from builtins import __import__ as load\nload('test_fixture')\n",
        "loader=__import__\nother=loader\nother('test_fixture')\n",
        "import importlib as lib\nloader=lib.import_module\nloader('test_fixture')\n",
        "import builtins as b\nloader=getattr(b, name)\nloader('test_fixture')\n",
        "loader=getattr(object(), '__import__')\nloader('test_fixture')\n",
        "loader=globals()['__import__']\nloader('test_fixture')\n",
        "loader=__builtins__['__import__']\nloader('test_fixture')\n",
        "import builtins as b\nloader=b.__dict__[name]\nloader('test_fixture')\n",
        "from builtins import *\n__import__('test_fixture')\n",
        "import runpy as runner\nrunner.run_module('test_fixture')\n",
    ],
)
def test_aliased_or_uncertain_loader_cannot_omit_fixture_consumer(
    tmp_path: Path, consumer: str
) -> None:
    tree(tmp_path, test_fixture="def fixture(): return 1\n", test_consumer=consumer)
    manifest = select(tmp_path, (Change("M", "tests/test_fixture.py"),))
    assert manifest.mode == "full-required" and not manifest.selected_modules
    assert manifest.document()["pytest_arguments"] == []
    assert "dynamic_test_dependency:tests/test_consumer.py" in manifest.reasons
