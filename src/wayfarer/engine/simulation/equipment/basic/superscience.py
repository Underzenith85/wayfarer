"""Superscience melee and shield rows, kept apart from the audited TL listings."""

from wayfarer.engine.simulation.equipment.basic.rows import melee, source, weapon
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile

# The selected table prints ``^`` instead of an integer TL for superscience.
# These rows retain their exact inventory facts but remain selection-blocked.
SUPERSCIENCE_MELEE = (
    weapon(
        "force-sword",
        272,
        "superscience",
        10000,
        2000,
        melee(
            "force-sword-swing",
            "force-sword",
            3,
            "fixed",
            0,
            "burn",
            (1, 2),
            dice=8,
            divisor="5",
        ),
        unsupported=("superscience-technology-level",),
    ),
    EquipmentProfile(
        definition_id="equipment:monowire-whip",
        provenance=source(272),
        technology_level="superscience",
        price=900,
        weight_millipounds=500,
        unsupported_mechanics=(
            "superscience-technology-level",
            "extra-die-strength-damage",
            "variable-reach-ready",
        ),
    ),
)

SUPERSCIENCE_SHIELDS = (
    EquipmentProfile(
        definition_id="equipment:force-shield",
        provenance=source(287),
        technology_level="superscience",
        price=1500,
        weight_millipounds=500,
        unsupported_mechanics=(
            "superscience-technology-level",
            "shield-material-variants",
        ),
    ),
)
