from pathlib import Path

from scripts.select_pr_tests import select


def write(root: Path, relative: str, content: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def seed_invariants(root: Path) -> None:
    for relative in (
        "test_architecture.py",
        "test_command_entropy.py",
        "test_contracts.py",
        "test_profiles.py",
        "test_release_invariants.py",
        "test_replay.py",
    ):
        write(root, f"tests/{relative}")


def test_selects_transitive_consumers_and_invariants(tmp_path: Path) -> None:
    seed_invariants(tmp_path)
    write(tmp_path, "src/wayfarer/engine/rule.py", "VALUE = 1\n")
    write(
        tmp_path,
        "src/wayfarer/orchestration/service.py",
        "from wayfarer.engine.rule import VALUE\n",
    )
    write(
        tmp_path,
        "tests/test_service.py",
        "from wayfarer.orchestration.service import VALUE\n",
    )

    result = select(["src/wayfarer/engine/rule.py"], tmp_path)

    assert result.mode == "focused"
    assert "tests/test_service.py" in result.tests
    assert "tests/test_replay.py" in result.tests


def test_shared_configuration_forces_full_suite(tmp_path: Path) -> None:
    result = select(["pyproject.toml"], tmp_path)

    assert result.python_changed
    assert result.mode == "full"


def test_unknown_python_dependency_fails_open_to_full_suite(tmp_path: Path) -> None:
    seed_invariants(tmp_path)
    write(tmp_path, "src/wayfarer/unused.py", "VALUE = 1\n")

    result = select(["src/wayfarer/unused.py"], tmp_path)

    assert result.mode == "full"
    assert "no static test dependency" in result.reason


def test_frontend_only_change_skips_python(tmp_path: Path) -> None:
    result = select(["frontend/src/play/store.ts"], tmp_path)

    assert not result.python_changed
    assert result.frontend_changed
    assert result.mode == "none"


def test_contract_change_requires_both_stacks(tmp_path: Path) -> None:
    result = select(["contracts/tactical/v1/openapi.json"], tmp_path)

    assert result.python_changed
    assert result.frontend_changed
    assert result.mode == "full"
