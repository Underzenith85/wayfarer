"""Printed B253 purity ends on contamination; added volume does not clean it."""

import pytest

from wayfarer.engine.simulation.magic.water_purification import receiving_mixture
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import ValidationError


def vessel(gallons: int, pure: int, *, capacity: int = 10) -> WaterBody:
    return WaterBody(
        object_id="chest",
        location_id="dock",
        gallons=gallons,
        pure_gallons=pure,
        nature="container",
        capacity_gallons=capacity,
    )


@pytest.mark.parametrize("initial,pure,final_pure", [(0, 0, 2), (1, 1, 3), (1, 0, 0), (3, 2, 0)])
def test_mixing_uses_actual_receiver_impurity_without_dilution_threshold(
    initial: int, pure: int, final_pure: int
) -> None:
    before = vessel(initial, pure)
    after = receiving_mixture(before, 2)
    assert (after.gallons, after.pure_gallons) == (initial + 2, final_pure)
    assert after.object_id == before.object_id and after.location_id == before.location_id
    assert before.gallons == initial


def test_current_vessel_capacity_still_limits_actual_flow() -> None:
    with pytest.raises(ValidationError, match="current capacity"):
        receiving_mixture(vessel(2, 0, capacity=3), 2)
