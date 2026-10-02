"""Independent B251 thresholds in millipounds, without rounded mass discounts."""

from fractions import Fraction

import pytest

from wayfarer.engine.simulation.magic.apportation_bindings import energy_for_weight
from wayfarer.errors import ValidationError


@pytest.mark.parametrize(
    ("weight", "energy"),
    [
        (1, 1),
        (1000, 1),
        (1001, 2),
        (10000, 2),
        (10001, 3),
        (50000, 3),
        (50001, 4),
        (200000, 4),
        (200001, 8),
        (300000, 8),
        (300001, 12),
        (400000, 12),
        (Fraction(200000001, 1000), 8),
    ],
)
def test_apportation_weight_cost(weight: int | Fraction, energy: int) -> None:
    assert energy_for_weight(weight) == energy


@pytest.mark.parametrize("weight", [0, -1])
def test_nonphysical_weight_rejected(weight: int) -> None:
    with pytest.raises(ValidationError):
        energy_for_weight(weight)
