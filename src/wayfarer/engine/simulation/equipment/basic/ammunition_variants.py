"""Explicit B279-B280 small-arms ammunition inventory choices."""

from decimal import Decimal

from wayfarer.engine.rules.types.ranged_equipment import AmmunitionVariant
from wayfarer.engine.simulation.equipment.basic.ammunition import (
    FIREARM_AMMUNITION,
    LONG_GUN_AMMUNITION,
)
from wayfarer.engine.simulation.equipment.basic.firearms import FIREARMS
from wayfarer.engine.simulation.equipment.basic.handguns import ORDINARY_HANDGUNS
from wayfarer.engine.simulation.equipment.basic.higher_tl import (
    HIGHER_TL_AMMUNITION,
    HIGHER_TL_WEAPONS,
)
from wayfarer.engine.simulation.equipment.basic.rifles import REPEATING_RIFLES
from wayfarer.engine.simulation.equipment.basic.rows import source
from wayfarer.engine.simulation.equipment.basic.smgs import ORDINARY_SMGS
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile, RangedMode

_weapons = FIREARMS + ORDINARY_HANDGUNS + ORDINARY_SMGS + REPEATING_RIFLES + HIGHER_TL_WEAPONS
_ammo = {
    entry.definition_id: entry
    for entry in FIREARM_AMMUNITION + LONG_GUN_AMMUNITION + HIGHER_TL_AMMUNITION
}
_damage_all = {
    mode.ammunition_id: mode.damage.damage_type
    for entry in _weapons
    for mode in entry.modes
    if isinstance(mode, RangedMode)
    and mode.ammunition_id in _ammo
    and mode.firearm is not None
    and mode.firearm.action in ("repeating", "revolver", "breechloader", "muzzleloader")
    and mode.skill_id in ("skill:guns-pistol", "skill:guns-smg", "skill:guns-rifle")
    and mode.damage.armor_divisor == 1
}
_degrade = {"pi++": "pi+", "pi+": "pi", "pi": "pi-", "pi-": "pi-"}


def _variant(
    base: EquipmentProfile,
    suffix: str,
    *,
    tl: int,
    cost_multiplier: int,
    damage_type: str,
    divisor: str,
    legality: int | None = None,
    range_multiplier: str = "1",
    add_per_die: int = 0,
) -> EquipmentProfile:
    return EquipmentProfile(
        definition_id=base.definition_id + "-" + suffix,
        provenance=source((279, 280)),
        weight_millipounds=base.weight_millipounds,
        price=base.price * cost_multiplier,
        technology_level=max(
            tl, base.technology_level if isinstance(base.technology_level, int) else tl
        ),
        legality_class=legality,
        ammunition=True,
        ammunition_variant=AmmunitionVariant(
            base_definition_id=base.definition_id,
            damage_type=damage_type,  # type: ignore[arg-type]
            armor_divisor=Decimal(divisor),
            damage_add_per_die=add_per_die,
            range_multiplier=Decimal(range_multiplier),
        ),
    )


SMALL_ARMS_VARIANTS = tuple(
    variant
    for definition_id, damage_type in sorted(_damage_all.items())
    for base in (_ammo[definition_id],)
    for variant in (
        *(
            ()
            if damage_type == "pi++"
            else (
                _variant(
                    base,
                    "hp",
                    tl=6,
                    cost_multiplier=1,
                    damage_type={"pi-": "pi", "pi": "pi+", "pi+": "pi++"}[damage_type],
                    divisor="0.5",
                ),
            )
        ),
        _variant(
            base,
            "aphc",
            tl=7,
            cost_multiplier=2,
            damage_type=_degrade[damage_type],
            divisor="2",
            legality=2,
        ),
        _variant(
            base,
            "apds",
            tl=9,
            cost_multiplier=5,
            damage_type=_degrade[damage_type],
            divisor="2",
            legality=1,
            range_multiplier="1.5",
            add_per_die=1,
        ),
    )
)
