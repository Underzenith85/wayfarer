"""The Basic Set equipment catalog, assembled from the audited listings."""

from wayfarer.engine.simulation.equipment.basic.ammunition import (
    FIREARM_AMMUNITION,
    LONG_GUN_AMMUNITION,
    SHOTGUN_AMMUNITION,
)
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR, SHIELDS
from wayfarer.engine.simulation.equipment.basic.electronics import ELECTRONICS, ELECTRONICS_IDS
from wayfarer.engine.simulation.equipment.basic.firearms import FIREARMS
from wayfarer.engine.simulation.equipment.basic.gear import ORDINARY
from wayfarer.engine.simulation.equipment.basic.handguns import ORDINARY_HANDGUNS
from wayfarer.engine.simulation.equipment.basic.higher_tl import (
    HIGHER_TL_AMMUNITION,
    HIGHER_TL_WEAPONS,
)
from wayfarer.engine.simulation.equipment.basic.melee import WEAPONS
from wayfarer.engine.simulation.equipment.basic.muscle_powered import (
    MUSCLE_POWERED_AMMUNITION,
    MUSCLE_POWERED_RANGED,
)
from wayfarer.engine.simulation.equipment.basic.rifles import REPEATING_RIFLES
from wayfarer.engine.simulation.equipment.basic.smgs import ORDINARY_SMGS
from wayfarer.engine.simulation.equipment.basic.superscience import (
    SUPERSCIENCE_MELEE,
    SUPERSCIENCE_SHIELDS,
)
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog

BASIC_EQUIPMENT = EquipmentCatalog(
    profile_id="gurps-basic-set-4e-2004",
    entries=(
        WEAPONS
        + SUPERSCIENCE_MELEE
        + MUSCLE_POWERED_RANGED
        + MUSCLE_POWERED_AMMUNITION
        + FIREARMS
        + ORDINARY_HANDGUNS
        + ORDINARY_SMGS
        + REPEATING_RIFLES
        + FIREARM_AMMUNITION
        + LONG_GUN_AMMUNITION
        + SHOTGUN_AMMUNITION
        + HIGHER_TL_WEAPONS
        + HIGHER_TL_AMMUNITION
        + ARMOR
        + SHIELDS
        + SUPERSCIENCE_SHIELDS
        + tuple(row for row in ORDINARY if row.definition_id not in ELECTRONICS_IDS)
        + ELECTRONICS
    ),
)
