"""B235/B249 purchased spells: real IQ/Hard levels and no free defaults."""

import pytest
from test_analyze_magic_construction import compiled


@pytest.mark.parametrize("spell", ("detect-magic", "identify-spell"))
@pytest.mark.parametrize("points,expected", ((1, 10), (2, 11), (4, 12), (8, 13), (12, 14)))
def test_purchased_detect_identify_compile_actual_levels(
    spell: str, points: int, expected: int
) -> None:
    purchases = (("detect-magic", 1),) if spell == "identify-spell" else ()
    result = compiled((*purchases, (spell, points)))
    assert result.legal and result.build is not None, result.diagnostics
    assert (
        next(v.value for v in result.build.sheet.values if v.target == "spell:" + spell) == expected
    )


def test_high_magery_does_not_replace_purchased_detect_prerequisite() -> None:
    result = compiled((("identify-spell", 12),), magery=3)
    assert not result.legal and result.build is None


def test_magery_zero_cannot_purchase_detect_even_with_high_skill_points() -> None:
    result = compiled((("detect-magic", 12),), magery=0)
    assert not result.legal and result.build is None


@pytest.mark.parametrize(
    "purchases,absent",
    (((), ("detect-magic", "identify-spell")), ((("detect-magic", 12),), ("identify-spell",))),
)
def test_unpurchased_spell_is_not_compiled_as_iq_or_prerequisite_default(
    purchases: tuple[tuple[str, int], ...], absent: tuple[str, ...]
) -> None:
    result = compiled(purchases, magery=3)
    assert result.legal and result.build is not None, result.diagnostics
    actual = {value.target for value in result.build.sheet.values}
    assert all("spell:" + spell not in actual for spell in absent)
