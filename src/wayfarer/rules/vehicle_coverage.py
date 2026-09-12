"""Per-mode Basic Set vehicle coverage audit (#358).

`gurps.vehicles.movement` and `gurps.vehicles.combat` were declared `partial`
under #120, which closed after landing a ground slice only, and #207, which
closed after expanding the modes. Neither issue is available to own what is still
missing, so this module states it as data: one row per declared locomotion mode,
naming the operations that mode actually carries, the concerns it resolves, and
every residual with the concrete open issue that owns it.

Nothing here implements a mechanic. Its job is that the declared capability
status cannot drift from the audit: the status is derived from the rows, the
operation set is reconciled against :data:`VEHICLE_OPERATIONS`, and a residual
without a live owner is a coverage failure rather than a silent gap.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.rules.vehicle_capabilities import VEHICLE_OPERATIONS

OWNER: Final = 358
MOVEMENT: Final = "gurps.vehicles.movement"
COMBAT: Final = "gurps.vehicles.combat"
# The issues this audit split its residual scope into. A closed owner cannot
# hold a blocker, which is why #120 and #207 are superseded rather than cited.
SUPERSEDED: Final = (120, 207)
AIR_MOVEMENT_OWNER: Final = 393
WATER_CASUALTY_OWNER: Final = 394
SPACE_MOVEMENT_OWNER: Final = 395
MOUNTED_OWNER: Final = 396
VEHICLE_COMBAT_OWNER: Final = 397


class Concern(StrEnum):
    """What a locomotion mode must resolve before it can be claimed verified."""

    CONTROL_LOSS = "control-loss"
    COLLISION = "collision"
    OCCUPANT_INJURY = "occupant-injury"
    RESTART = "restart"


ALL_CONCERNS: Final = tuple(Concern)


@dataclass(frozen=True, slots=True)
class ModeCoverage:
    """One declared locomotion mode and exactly what it does and does not carry."""

    mode: str
    reference: str
    concerns: tuple[Concern, ...] = ()
    # Named scope this mode does not carry, each mapped to the open issue that
    # owns it. A residual without an owner is a coverage failure, not a gap.
    residuals: Mapping[str, int] = field(default_factory=dict)

    @property
    def operations(self) -> frozenset[str]:
        """The operations the adapter actually offers, never a second claim."""
        return VEHICLE_OPERATIONS[self.mode]

    @property
    def verified(self) -> bool:
        """A mode is verified only once it resolves every concern and owes nothing."""
        return set(self.concerns) == set(ALL_CONCERNS) and not self.residuals

    @property
    def owners(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(self.residuals.values()))


# B469 ground control loss splits margin against stability into a skid or a
# crash, B430-B432 resolve the collision exchange and the occupants, and the
# restart path is the shared transport transaction.
GROUND_RESIDUALS: Final[Mapping[str, int]] = MappingProxyType({})
GROUND_MODES: Final = (
    "ground-wheeled",
    "ground-tracked",
    "ground-drawn",
    "ground-walking",
    "ground-slithering",
)


def _ground(mode: str) -> ModeCoverage:
    return ModeCoverage(mode, "B394-B395, B430-B432, B469", ALL_CONCERNS, GROUND_RESIDUALS)


_MODES: Final = (
    *(_ground(mode) for mode in GROUND_MODES),
    ModeCoverage(
        "water",
        "B430-B432, B466, B469",
        ALL_CONCERNS,
        {
            "sinking rate and the time it leaves occupants": WATER_CASUALTY_OWNER,
            "capsizing recovery for an unsinkable craft": WATER_CASUALTY_OWNER,
            "hull leaks and their progression": WATER_CASUALTY_OWNER,
            "open-deck overboard checks": WATER_CASUALTY_OWNER,
            "water currents and fractional draft": WATER_CASUALTY_OWNER,
        },
    ),
    ModeCoverage(
        "underwater",
        "B430-B432, B466, B469",
        ALL_CONCERNS,
        {
            "underwater stress damage below the stress-failure threshold": WATER_CASUALTY_OWNER,
            "hull leaks and their progression": WATER_CASUALTY_OWNER,
            "occupant decompression": WATER_CASUALTY_OWNER,
        },
    ),
    ModeCoverage(
        "air",
        "B394-B395, B430-B432, B468-B469",
        ALL_CONCERNS,
        {
            "vertical flight and climbing or diving trajectories": AIR_MOVEMENT_OWNER,
            "a stall or dive continuing across turns, and the fall itself": AIR_MOVEMENT_OWNER,
            "airborne drift": AIR_MOVEMENT_OWNER,
            "terrain-relative air-crash consequences": AIR_MOVEMENT_OWNER,
        },
    ),
    # A spacecraft can lose control and can collide, but it cannot travel: there
    # is no drag to brake against, so `safe_deceleration` rejects it outright.
    ModeCoverage(
        "space",
        "B430-B432, B466-B470",
        ALL_CONCERNS,
        {
            "thrust as acceleration, with no borrowed braking envelope": SPACE_MOVEMENT_OWNER,
            "navigation at the speed and distance scales a spacecraft uses": SPACE_MOVEMENT_OWNER,
            "fuel and delta-v as a consumed resource": SPACE_MOVEMENT_OWNER,
            "the very large speed and damage scales a space collision reaches": (
                SPACE_MOVEMENT_OWNER
            ),
        },
    ),
    # The B397 spooked-mount check executes, but it belongs to the version-one
    # adapter; every version-two mounted path rejects by name.
    ModeCoverage(
        "ground-mount",
        "B397, B466-B470",
        (),
        {
            "mounted movement keyed to the mount's own move": MOUNTED_OWNER,
            "mount control through Riding and the mounted loss table": MOUNTED_OWNER,
            "rider separation on a fall or a collision": MOUNTED_OWNER,
        },
    ),
)
MODES: Final = MappingProxyType({entry.mode: entry for entry in _MODES})
# `gurps.vehicles.combat` has no implementation behind it at all: the adapter
# records what combat would consume, and nothing consumes it.
COMBAT_RESIDUALS: Final = MappingProxyType(
    {
        "ramming as a declared attack with its own defense": VEHICLE_COMBAT_OWNER,
        "vehicle-mounted weapons and the pose they fire from": VEHICLE_COMBAT_OWNER,
        "cover a vehicle gives its occupants": VEHICLE_COMBAT_OWNER,
        "Aim and attack-penalty consumption the control state records": VEHICLE_COMBAT_OWNER,
        "synchronised encounter poses on one battlefield and one turn clock": VEHICLE_COMBAT_OWNER,
        "vehicle hit locations, operator incapacitation, ongoing stress below zero HP "
        "and disabled equipment": VEHICLE_COMBAT_OWNER,
    }
)


def movement_status() -> CoverageStatus:
    """Verified only when every declared mode is; one residual keeps it partial."""
    return (
        CoverageStatus.VERIFIED
        if all(entry.verified for entry in _MODES)
        else CoverageStatus.PARTIAL
    )


def combat_status() -> CoverageStatus:
    return CoverageStatus.PARTIAL if COMBAT_RESIDUALS else CoverageStatus.VERIFIED


def residual_owners() -> tuple[int, ...]:
    """Every live issue this audit still depends on, for the coverage report."""
    owners = {owner for entry in _MODES for owner in entry.owners}
    return tuple(sorted(owners | set(COMBAT_RESIDUALS.values())))


def validate_coverage() -> None:
    """The registry, the operation matrix and this audit cannot drift apart."""
    if set(MODES) != set(VEHICLE_OPERATIONS):
        raise ValidationError(
            "Vehicle coverage audit and the operation matrix declare different modes"
        )
    for entry in _MODES:
        if len(set(entry.concerns)) != len(entry.concerns):
            raise ValidationError(f"Duplicate vehicle concern: {entry.mode}")
        if not entry.reference.startswith("B"):
            raise ValidationError(f"Vehicle mode needs a source reference: {entry.mode}")
        if not entry.operations and entry.concerns:
            raise ValidationError(f"Mode resolves a concern with no operation: {entry.mode}")
        if any(owner in (0, OWNER, *SUPERSEDED) for owner in entry.residuals.values()):
            raise ValidationError(f"Vehicle residual names no live owner: {entry.mode}")
    if any(owner in (0, OWNER, *SUPERSEDED) for owner in COMBAT_RESIDUALS.values()):
        raise ValidationError("Vehicle combat residual names no live owner")
    for identifier, derived in ((MOVEMENT, movement_status()), (COMBAT, combat_status())):
        declared = CAPABILITIES[identifier]
        if declared.status is not derived:
            raise ValidationError(f"Declared capability disagrees with the audit: {identifier}")
        if declared.owner_issue != OWNER:
            raise ValidationError(f"Capability owner is not this audit: {identifier}")


def audit_report() -> dict[str, object]:
    """Publish the audit to the scenario, character and LLM validators."""
    validate_coverage()
    return {
        "owner": OWNER,
        "supersedes": list(SUPERSEDED),
        "capabilities": {
            MOVEMENT: movement_status().value,
            COMBAT: combat_status().value,
        },
        "modes": [
            {
                "mode": entry.mode,
                "reference": entry.reference,
                "operations": sorted(entry.operations),
                "concerns": [concern.value for concern in entry.concerns],
                "residuals": dict(entry.residuals),
                "verified": entry.verified,
            }
            for entry in _MODES
        ],
        "combat_residuals": dict(COMBAT_RESIDUALS),
        "residual_owners": list(residual_owners()),
        "verified_modes": sum(entry.verified for entry in _MODES),
        "total_modes": len(_MODES),
    }
