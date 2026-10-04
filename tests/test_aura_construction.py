"""B235/B249 Aura IQ/Hard and genuinely learned Detect prerequisite."""

import pytest
from test_analyze_magic_construction import compiled

from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, PrerequisiteKind


@pytest.mark.parametrize("points,expected", [(1, 10), (2, 11), (4, 12), (8, 13), (12, 14)])
def test_actual_aura_learning(points: int, expected: int) -> None:
    result = compiled((("detect-magic", 1), ("aura", points)))
    assert result.legal and result.build is not None
    assert next(v.value for v in result.build.sheet.values if v.target == "spell:aura") == expected
    spec = next(d.skill for d in package().definitions if d.id == "spell:aura")
    assert (
        spec is not None
        and spec.attribute == ControllingAttribute.IQ
        and spec.difficulty == Difficulty.HARD
    )
    assert len(spec.prerequisites) == 1
    assert spec.prerequisites[0].target == "spell:detect-magic"
    assert spec.prerequisites[0].kind == PrerequisiteKind.PURCHASED_DEFINITION


@pytest.mark.parametrize("missing", ["detect", "magery"])
def test_aura_cannot_replace_purchased_prerequisite_with_high_points(missing: str) -> None:
    result = compiled(
        (("aura", 16),) if missing == "detect" else (("detect-magic", 16), ("aura", 16)),
        magery=0 if missing == "magery" else 3,
    )
    assert not result.legal and result.build is None
