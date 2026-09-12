"""Executable electronics rows from Characters B288-289 and Campaigns B471-472."""

from wayfarer.engine.rules.types.electronics import (
    CommunicatorSpec,
    ComputerSpec,
    ElectronicsSuite,
    SensorSpec,
)
from wayfarer.engine.simulation.equipment.basic.gear import ORDINARY
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile


def _row(identifier: str, suite: ElectronicsSuite) -> EquipmentProfile:
    profile = next(row for row in ORDINARY if row.definition_id == "equipment:" + identifier)
    remaining = tuple(
        blocker
        for blocker in profile.unsupported_mechanics
        if blocker not in {"battery-and-computer-operation", "special-tool-effects"}
    )
    return profile.model_copy(update={"electronics": suite, "unsupported_mechanics": remaining})


ELECTRONICS = (
    _row(
        "cell-phone",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("voice", "text", "data"), requires_address=True),
            computer=ComputerSpec(complexity=1, storage_megabytes=1_000),
            power_capacity_seconds=10 * 60 * 60,
        ),
    ),
    _row(
        "backpack-radio",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("code", "voice", "data"), range_yards=20 * 1_760),
            power_capacity_seconds=12 * 60 * 60,
        ),
    ),
    _row(
        "hand-radio",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("voice",), range_yards=2 * 1_760),
            power_capacity_seconds=12 * 60 * 60,
        ),
    ),
    _row(
        "headset-radio",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("voice",), range_yards=1_760),
            power_capacity_seconds=12 * 60 * 60,
        ),
    ),
    _row(
        "secure-headset-radio",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("voice",), range_yards=1_760, secure=True),
            power_capacity_seconds=12 * 60 * 60,
        ),
    ),
    _row(
        "satellite-phone",
        ElectronicsSuite(
            communicator=CommunicatorSpec(media=("voice", "text", "data"), requires_address=True),
            power_capacity_seconds=60 * 60,
        ),
    ),
    # B472 gives representative 2004-era computers Complexity 2-4.  Storage is
    # authored explicitly rather than inferred from Complexity.
    _row(
        "laptop",
        ElectronicsSuite(
            computer=ComputerSpec(complexity=3, storage_megabytes=100_000),
            power_capacity_seconds=2 * 60 * 60,
        ),
    ),
    _row(
        "wearable-computer",
        ElectronicsSuite(
            computer=ComputerSpec(complexity=2, storage_megabytes=10_000),
            power_capacity_seconds=8 * 60 * 60,
        ),
    ),
    _row(
        "metal-detector-wand",
        ElectronicsSuite(
            sensor=SensorSpec(form="manual", sense="metal-detection", active=True, new_sense=True),
            power_capacity_seconds=8 * 60 * 60,
        ),
    ),
    _row(
        "night-vision-goggles",
        ElectronicsSuite(
            sensor=SensorSpec(form="hands-free", sense="night-vision", night_vision=9),
            power_capacity_seconds=8 * 60 * 60,
        ),
    ),
)

ELECTRONICS_IDS = frozenset(profile.definition_id for profile in ELECTRONICS)
