"""B464 vehicle listing, deliberately separate from carried equipment."""

from pydantic import Field

from wayfarer.engine.rules.types.vehicle import Locomotion
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
    locomotion: Locomotion
    handling: int = Field(ge=-10, le=10)
    stability: int = Field(ge=1, le=10)
    acceleration: int = Field(gt=0)
    top_speed: int = Field(gt=0)
    occupants: int = Field(gt=0)
    road_bound: bool = False
    required_capabilities: tuple[str, ...] = ("gurps.vehicles.movement", "gurps.vehicles.combat")

    def require_operation(self) -> None:
        raise ValidationError(
            "Vehicle operation requires an initialized owned body; use bind_vehicle"
        )


VEHICLE_INDEX = (
    VehicleEntry(
        definition_id="vehicle:wagon",
        technology_level=3,
        hp=35,
        dr=2,
        price=680,
        locomotion="ground-drawn",
        handling=-3,
        stability=4,
        acceleration=4,
        top_speed=8,
        occupants=1,
        road_bound=True,
    ),
    VehicleEntry(
        definition_id="vehicle:luxury-car",
        technology_level=8,
        hp=57,
        dr=5,
        price=30000,
        locomotion="ground-wheeled",
        handling=0,
        stability=4,
        acceleration=3,
        top_speed=57,
        occupants=5,
        road_bound=True,
    ),
)
