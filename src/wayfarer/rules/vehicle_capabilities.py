"""Operation-level transport support for trusted scenario and character validators.

These internal operations do not certify a locomotion mode or enable live play.
Full Basic Set capability rows remain partial until all audit consumers exist.
"""

from types import MappingProxyType

GROUND = frozenset(
    {
        "vehicle-maneuver",
        "vehicle-control",
        "vehicle-impact",
        "vehicle-skid",
        "vehicle-rollover",
        "vehicle-resolve-ejection",
    }
)
PLANAR = frozenset({"vehicle-maneuver", "vehicle-control", "vehicle-impact"})
AIR = PLANAR | frozenset({"vehicle-resolve-air-aftermath"})
WATER = PLANAR | frozenset({"vehicle-resolve-water-aftermath"})
COMBAT = frozenset({"vehicle-ram", "vehicle-damage"})
VEHICLE_OPERATIONS = MappingProxyType(
    {
        "ground-wheeled": GROUND | COMBAT,
        "ground-tracked": GROUND | COMBAT,
        "ground-drawn": GROUND | COMBAT,
        "ground-walking": GROUND | COMBAT,
        "ground-slithering": GROUND | COMBAT,
        "water": WATER | COMBAT,
        "underwater": WATER | COMBAT,
        "air": AIR | COMBAT,
        "space": frozenset({"vehicle-control", "vehicle-impact", "vehicle-space-navigation"})
        | COMBAT,
        "ground-mount": frozenset(
            {"vehicle-maneuver", "vehicle-control", "vehicle-resolve-mount-separation"}
        ),
    }
)
