"""Superscience melee and shield rows, kept apart from the audited TL listings."""

from wayfarer.engine.simulation.equipment.basic.rows import melee, source, weapon
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, Shield

# The selected table prints ``^`` instead of an integer TL for superscience.
# Campaign equipment allowlists provide the explicit setting authorization.
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
    ),
    EquipmentProfile(
        definition_id="equipment:monowire-whip",
        provenance=source(272),
        technology_level="superscience",
        price=900,
        weight_millipounds=500,
        slot="hand",
        modes=(
            melee(
                "monowire-whip-swing",
                "monowire-whip",
                5,
                "swing",
                -2,
                "cut",
                (1, 2, 3, 4, 5, 6, 7),
                parry=-2,
                unbalanced=True,
                divisor="10",
                bonus_dice=1,
                reach_requires_ready=True,
            ),
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
        legality_class=3,
        slot="shield",
        shield=Shield(
            skill_id="skill:shield-force",
            defense_bonus=3,
            can_rush=False,
            hardened_dr=True,
            dr=100,
            occupies_hand=False,
        ),
    ),
)
