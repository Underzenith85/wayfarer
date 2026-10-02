"""B253 permanent purity and explicitly selected receiver recontamination.

This is a material consequence, not a second casting or energy calculation.
The shared host owns current-state admission, approved skill and atomic receipt.
"""

from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import ValidationError


def receiving_mixture(receiver: WaterBody, gallons: int) -> WaterBody:
    """Pour pure water into the actual receiving vessel and mix its contents.

    B253 promises purity until recontamination. It gives no dilution threshold
    that would turn any impure liquid into pure liquid merely by adding water.
    The opted-in operation mixes the vessel, so every gallon of the resulting
    mixture is classified as impure whenever the original vessel was impure.
    """
    if type(gallons) is not int or gallons < 1:
        raise ValidationError("A supported purification flow requires positive whole gallons")
    if receiver.form != "liquid" or receiver.capacity_gallons is None:
        raise ValidationError("Purification requires a liquid receiving container")
    total = receiver.gallons + gallons
    if total > receiver.capacity_gallons:
        raise ValidationError("Receiving container lacks current capacity")
    return WaterBody.model_validate(
        {
            **receiver.model_dump(),
            "gallons": total,
            "pure_gallons": total if receiver.pure_gallons == receiver.gallons else 0,
        }
    )
