"""B464 vehicle listing, deliberately separate from carried equipment."""

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

# Vehicle listing is deliberately separate from wearable/carryable equipment.
# Campaigns fourth printing B464; masses are loaded tons, never inventory lbs.


class VehicleEntry(Record):
    definition_id: Id
    page: int = 464
    edition: str = "Campaigns Fourth Edition, fourth printing"
    technology_level: int = Field(ge=0)
    hp: int = Field(gt=0)
    dr: int = Field(ge=0)
    price: int = Field(ge=0)
    required_capabilities: tuple[str, ...] = ("gurps.vehicles.movement", "gurps.vehicles.combat")

    def require_operation(self) -> None:
        # #120 closed without the operation integration; #358 owns the two
        # capability rows a listed vehicle would need before it can be driven.
        raise ValidationError("Vehicle listing does not implement operation; see #358")


VEHICLE_INDEX = (
    VehicleEntry(definition_id="vehicle:wagon", technology_level=3, hp=35, dr=2, price=680),
    VehicleEntry(definition_id="vehicle:luxury-car", technology_level=8, hp=57, dr=5, price=30000),
)
