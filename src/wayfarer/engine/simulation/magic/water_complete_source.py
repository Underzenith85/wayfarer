"""B253 full-source purification: no unsupported parcel-order assumption."""

from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import ValidationError


def require_complete_source(source: WaterBody, gallons: int) -> None:
    """The whole committed batch must still be the whole actual liquid source."""
    if type(gallons) is not int or gallons < 1 or source.form != "liquid":
        raise ValidationError("Complete-source purification requires positive whole liquid gallons")
    if gallons != source.gallons:
        raise ValidationError("Complete-source purification must empty the current source")


def emptied_source(source: WaterBody, gallons: int) -> WaterBody:
    """Every parcel crosses the purifying ring, so no water remains in the source."""
    require_complete_source(source, gallons)
    return WaterBody.model_validate({**source.model_dump(), "gallons": 0, "pure_gallons": 0})
