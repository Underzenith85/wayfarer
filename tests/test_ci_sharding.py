"""Planner refuses incomplete identities and preserves backend/subtest parents."""

from pathlib import Path

import pytest

from scripts.plan_ci_shards import plan, weights_from_junit


def inventory(tmp_path: Path) -> list[str]:
    nodes: list[str] = []
    for module in ("alpha", "beta", "gamma", "delta", "new"):
        path = tmp_path / "tests" / f"test_{module}.py"
        path.parent.mkdir(exist_ok=True)
        path.touch()
        nodes.extend(
            f"tests/test_{module}.py::test_parent[{backend}]" for backend in ("sqlite", "postgres")
        )
    return nodes


def test_exact_deterministic_partition(tmp_path: Path) -> None:
    nodes = inventory(tmp_path)
    weights = {"tests/test_alpha.py": 80.0, "tests/test_beta.py": 50.0}
    result = plan(nodes, tmp_path, weights)
    assert result == plan(list(reversed(nodes)), tmp_path, weights)
    shards = result["shards"]
    assert isinstance(shards, list)
    assigned = [n for s in shards for n in s["nodes"]]
    assert sorted(assigned) == sorted(nodes) and len(set(assigned)) == len(nodes)
    for s in shards:
        for module in s["modules"]:
            assert sum(n.startswith(module + "::") for n in s["nodes"]) == 2
    assert result["unmeasured_weight_floor_seconds"] == 80.0


@pytest.mark.parametrize(
    "bad",
    [
        "../tests/test_a.py::test_x",
        "/tests/test_a.py::test_x",
        "tests/test_missing.py::test_x",
        "tests/test_alpha.py",
        "tests/test_alpha.py::",
        "tests//test_alpha.py::test_x",
        "tests/test_alpha.py::test_x\n",
    ],
)
def test_invalid_nodes_fail_closed(tmp_path: Path, bad: str) -> None:
    inventory(tmp_path)
    with pytest.raises(ValueError):
        plan([bad], tmp_path, {})


def test_empty_duplicate_and_invalid_weights(tmp_path: Path) -> None:
    nodes = inventory(tmp_path)
    for bad in ([], [nodes[0], nodes[0]]):
        with pytest.raises(ValueError):
            plan(bad, tmp_path, {})
    for seconds in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            plan(nodes, tmp_path, {"tests/test_alpha.py": seconds})


def test_junit_parent_time_preserved(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_alpha" name="test_parent[postgres]" time="8.5"><subtest/></testcase><testcase classname="tests.test_alpha" name="test_parent[sqlite]" time="3.5"/></testsuite></testsuites>'
    )
    assert weights_from_junit(path) == {"tests/test_alpha.py": 12.0}
    path.write_text('<testsuite><testcase classname="unknown" time="1"/></testsuite>')
    with pytest.raises(ValueError):
        weights_from_junit(path)


def test_zero_rounded_weights_still_distribute_modules(tmp_path: Path) -> None:
    nodes = inventory(tmp_path)
    modules = {n.split("::", 1)[0] for n in nodes}
    result = plan(nodes, tmp_path, dict.fromkeys(modules, 0.0))
    shards = result["shards"]
    assert isinstance(shards, list) and all(s["nodes"] for s in shards)
