"""Source-exact B275 silver construction and attack identity."""

from decimal import Decimal

from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import ResourceState, SilverConstruction
from wayfarer.errors import ValidationError


def attack_construction(
    resources: ResourceState, weapon_item_id: str, mode: RangedMode | None = None
) -> SilverConstruction | None:
    """Resolve material from the physical weapon or its pinned loaded arrowhead."""
    weapon = next((item for item in resources.items if item.id == weapon_item_id), None)
    if weapon is None:
        raise ValidationError("Attack weapon is absent from inventory")
    if mode is None or mode.thrown:
        return weapon.silver_construction
    load = next(
        (entry for entry in resources.ammunition_loads if entry.weapon_id == weapon_item_id), None
    )
    if load is None:
        return None
    ammunition = next(
        (item for item in resources.items if item.id == load.ammunition_item_id), None
    )
    if ammunition is None:
        raise ValidationError("Loaded attack ammunition is absent from inventory")
    return ammunition.silver_construction


def silver_wounding_multiplier(ordinary: int, construction: SilverConstruction | None) -> Decimal:
    """Apply B275's reduced Vulnerability multiplier for coating or edging."""
    if construction is None or ordinary == 1:
        return Decimal(1)
    if construction == "solid-silver":
        return Decimal(ordinary)
    try:
        return {2: Decimal("1.5"), 3: Decimal(2), 4: Decimal(3)}[ordinary]
    except KeyError as exc:
        raise ValidationError("Silver vulnerability requires a printed multiplier") from exc


def breakage_quality(
    construction: SilverConstruction | None,
    underlying: str | None,
) -> str | None:
    """Solid silver breaks as cheap; coating retains the underlying material."""
    return "cheap" if construction == "solid-silver" else underlying
