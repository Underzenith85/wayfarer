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
VEHICLE_OPERATIONS = MappingProxyType(
    {
        "ground-wheeled": GROUND,
        "ground-tracked": GROUND,
        "ground-drawn": GROUND,
        "ground-walking": GROUND,
        "ground-slithering": GROUND,
        "water": PLANAR,
        "underwater": PLANAR,
        "air": PLANAR,
        "space": frozenset({"vehicle-control", "vehicle-impact"}),
        "ground-mount": frozenset(),
    }
)
