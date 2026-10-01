"""B168 table, including asymmetry and impossible IQ equipment."""

import pytest

from wayfarer.engine.rules.skills.technology_level import technology_level_penalty
from wayfarer.engine.rules.types.skill import ControllingAttribute as A
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    "difference,penalty",
    [(-7, -13), (-4, -7), (-3, -5), (-2, -3), (-1, -1), (0, 0), (1, -5), (2, -10), (3, -15)],
)
def test_iq_table(difference: int, penalty: int) -> None:
    assert technology_level_penalty(7, 7 + difference, A.IQ) == penalty


def test_iq_four_tl_ahead_is_impossible() -> None:
    with pytest.raises(ValidationError, match="impossible"):
        technology_level_penalty(8, 12, A.IQ)


@pytest.mark.parametrize("attribute", [A.DX, A.HT, A.ST, A.PER, A.WILL])
@pytest.mark.parametrize("difference", [-4, -2, 0, 2, 4])
def test_non_iq_flat_penalty(attribute: A, difference: int) -> None:
    assert technology_level_penalty(6, 6 + difference, attribute) == -abs(difference)
