"""Independent printed-table and B85 examples; no rounded intermediate ratios."""

from fractions import Fraction

import pytest

from wayfarer.engine.rules.tables.size_forms import (
    dimension_yards,
    growth_minimum_st,
    height_ratio,
    reduced_result,
    shrinking_weight_ratio,
)
from wayfarer.errors import ValidationError


def test_printed_table_and_repeating_decades() -> None:
    expected = {
        -12: Fraction(1, 50),
        -10: Fraction(1, 20),
        -9: Fraction(7, 100),
        -8: Fraction(1, 10),
        -7: Fraction(3, 20),
        -6: Fraction(1, 5),
        -5: Fraction(3, 10),
        -4: Fraction(1, 2),
        -3: Fraction(7, 10),
        -2: Fraction(1),
        -1: Fraction(3, 2),
        0: Fraction(2),
        1: Fraction(3),
        2: Fraction(5),
        3: Fraction(7),
        4: Fraction(10),
        5: Fraction(15),
        6: Fraction(20),
        10: Fraction(100),
        11: Fraction(150),
    }
    for sm, yards in expected.items():
        assert dimension_yards(sm) == yards
        assert dimension_yards(sm + 6) == yards * 10


def test_growth_four_and_shrinking_twelve_source_examples() -> None:
    assert growth_minimum_st(0, 4) == 50
    assert height_ratio(0, 4) == 5
    assert height_ratio(0, -12) == Fraction(1, 100)
    assert shrinking_weight_ratio(12) == Fraction(1, 1_000_000)
    assert reduced_result(500, height_ratio(0, -12)) == 5
    assert height_ratio(0, -1) == Fraction(3, 4)
    assert shrinking_weight_ratio(1) == Fraction(1, 3)
    assert shrinking_weight_ratio(3) == Fraction(1, 30)
    assert reduced_result(5, Fraction(3, 4)) == 3
    assert reduced_result(3, Fraction(1, 2)) == 1
    assert reduced_result(1, Fraction(1, 100)) == 0
    # Exact geometry survives where integer grid movement has no full yard.
    assert height_ratio(-3, -1) == Fraction(5, 7)
    assert growth_minimum_st(-3, 1) == 5


@pytest.mark.parametrize("levels", [-1, True])
def test_invalid_levels_are_not_silently_coerced(levels: int) -> None:
    with pytest.raises(ValidationError):
        shrinking_weight_ratio(levels)
    with pytest.raises(ValidationError):
        growth_minimum_st(0, levels)
