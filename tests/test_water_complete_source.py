"""Whole-source conservation does not depend on unknown parcel ordering."""

import pytest

from wayfarer.engine.simulation.magic.water_complete_source import emptied_source
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import ValidationError


@pytest.mark.parametrize("pure", [0, 1, 2, 3])
def test_every_parcel_leaves_fully_consumed_source(pure: int) -> None:
    source = WaterBody(
        object_id="hidden",
        location_id="dock",
        gallons=3,
        pure_gallons=pure,
        nature="water source",
        significant=True,
    )
    result = emptied_source(source, 3)
    assert (result.gallons, result.pure_gallons) == (0, 0)
    assert result.object_id == source.object_id and result.location_id == source.location_id
    assert source.gallons == 3 and source.pure_gallons == pure


@pytest.mark.parametrize("remaining", [2, 4])
def test_changed_current_source_cannot_be_treated_as_full_committed_batch(remaining: int) -> None:
    source = WaterBody(
        object_id="hidden",
        location_id="dock",
        gallons=remaining,
        pure_gallons=1,
        nature="water source",
    )
    with pytest.raises(ValidationError, match="current source"):
        emptied_source(source, 3)
